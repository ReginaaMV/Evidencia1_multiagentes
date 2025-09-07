import agentpy as ap
import socket, json, time

HOST, PORT = "127.0.0.1", 5005
_sock = None

# ---------- Conexión TCP persistente ----------
def open_conn(retries=30, delay=1.0):
    """Conecta con el servidor TCP de Unity (Unity debe estar en Play)."""
    global _sock
    for i in range(retries):
        try:
            _sock = socket.create_connection((HOST, PORT), timeout=2)
            print(f"[PY] Conectado a Unity en el intento {i+1}")
            return
        except OSError:
            print(f"[PY] Unity no responde ({i+1}/{retries}). Reintento en {delay}s...")
            time.sleep(delay)
    raise RuntimeError("[PY] No se pudo conectar con Unity (¿está en Play?)")

def close_conn():
    global _sock
    try:
        if _sock:
            _sock.close()
    except:
        pass
    _sock = None

def send(payload: dict):
    """Envía JSON por la MISMA conexión, delimitado por '\\n'."""
    msg = json.dumps(payload) + "\n"
    _sock.sendall(msg.encode("utf-8"))
    print("[PY->UNITY]", msg.strip())

# ============================ AGENTES ============================
class Light(ap.Agent):
    """
    Semáforo:
    GREEN -> AMBER -> RED.
    En RED: NO pasa a GREEN hasta que el peatón termine (ped.finished=True)
    y se cumpla un mínimo de rojo (min_red_steps).
    """
    def setup(self):
        self.phase = "GREEN"
        self.counter = 0
        self.min_red = self.p.min_red_steps
        self.dur = {
            "GREEN": self.p.green_steps,
            "AMBER": self.p.amber_steps,
            "RED": self.p.red_steps
        }

    def step(self):
        ped = self.model.ped
        if self.phase == "GREEN" and self.counter >= self.dur["GREEN"]:
            self.phase = "AMBER"; self.counter = 0
        elif self.phase == "AMBER" and self.counter >= self.dur["AMBER"]:
            self.phase = "RED"; self.counter = 0
        elif self.phase == "RED":
            min_ok = (self.counter >= self.min_red)
            ped_done = ped.finished
            if min_ok and ped_done:
                self.phase = "GREEN"; self.counter = 0

        send({"type": "light", "state": self.phase, "step": int(self.model.t)})
        self.counter += 1
        return self.phase

    def reset(self):
        self.phase = "GREEN"
        self.counter = 0
        send({"type": "light", "state": self.phase, "step": int(self.model.t)})

class Car(ap.Agent):
    """
    Carro:
    - Antes de 'liberarse':
        ROJO     -> alto total
        AMARILLO -> disminuye velocidad (no alto)
        VERDE    -> avanza (puede tener retardo de reacción); al avanzar por primera vez en VERDE,
                    el coche queda 'liberado' y NUNCA vuelve a frenar.
    - Después de 'liberarse' (released=True):
        Ignora el semáforo para siempre y sigue a velocidad de crucero.
    """
    def setup(self):
        self.agent_id = "car_1"
        self.x0, self.z0 = -14.53, -0.11
        self.x, self.z = self.x0, self.z0

        # Velocidades
        self.v_green = self.p.car_speed
        self.v_amber = self.p.car_speed * self.p.amber_factor  # p.ej. 0.5

        # Estado de liberación
        self.released = False
        self.was_stopped_once = False

        # Retardo al VERDE
        self.reaction_delay = self.p["reaction_delay_steps"] if "reaction_delay_steps" in self.p else 0
        self.reaction_counter = 0

        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action":"INICIO","step":0})

    def step(self, light_state):
        # El carro debe frenar la PRIMERA vez que ve rojo, disminuir en ámbar,
        # y solo después de haberse detenido y luego ver verde, se libera para siempre.

        if self.released:
            self.x += self.v_green
            action = "CONTINUA"
        else:
            if not self.was_stopped_once:
                if light_state == "RED":
                    self.reaction_counter = 0
                    self.was_stopped_once = True
                    action = "ESPERA"
                elif light_state == "AMBER":
                    self.reaction_counter = 0
                    self.x += self.v_amber
                    action = "DISMINUYE"
                else:  # GREEN
                    self.x += self.v_green
                    action = "AVANZA_INICIO"
            else:
                if light_state == "RED":
                    self.reaction_counter = 0
                    action = "ESPERA"
                elif light_state == "AMBER":
                    self.reaction_counter = 0
                    self.x += self.v_amber
                    action = "DISMINUYE"
                else:  # GREEN
                    if self.reaction_counter < self.reaction_delay:
                        self.reaction_counter += 1
                        action = "REACTION"
                    else:
                        self.x += self.v_green
                        self.released = True
                        print("[CAR] LIBERADO: a partir de ahora ya no vuelve a frenar.")
                        action = "AVANZA"

        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action": action, "step": int(self.model.t)})

    def reset(self):
        self.x, self.z = self.x0, self.z0
        self.released = False
        self.was_stopped_once = False
        self.reaction_counter = 0
        print("[CAR] RESET a posición inicial (requiere nueva liberación)")
        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action":"REINICIO","step": int(self.model.t)})

class Pedestrian(ap.Agent):
    """
    Peatón:
    - Arranca a cruzar en ROJO.
    - Una vez que empieza: NUNCA se detiene (ignora semáforo) y sigue caminando.
    - Al llegar a ped_end_z marca finished=True (permite pasar a GREEN), pero sigue avanzando.
    """
    def setup(self):
        self.agent_id = "pedestrian"
        self.x0, self.z0 = -0.70, -7.22
        self.x, self.z = self.x0, self.z0
        self.speed = self.p.ped_speed
        self.ped_end_z = self.p.ped_end_z
        self.started_crossing = False
        self.finished = False
        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action":"INICIO","step":0})

    def step(self, light_state):
        if not self.started_crossing and light_state == "RED":
            self.started_crossing = True
            print("[PED] Comienza a cruzar")

        if self.started_crossing:
            self.z += self.speed
            if not self.finished and self.z >= self.ped_end_z:
                self.finished = True
                print(f"[PED] Terminó de cruzar en z={self.z:.3f}")
            action = "CRUZA" if not self.finished else "SIGUE"
        else:
            action = "ESPERA"

        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action": action, "step": int(self.model.t)})

    def reset(self):
        self.x, self.z = self.x0, self.z0
        self.started_crossing = False
        self.finished = False
        print("[PED] RESET a posición inicial")
        send({"type":"state","agent_id":self.agent_id,"x":self.x,"z":self.z,
              "action":"REINICIO","step": int(self.model.t)})

class CrossModel(ap.Model):
    def setup(self):
        self.light = Light(self)
        self.car = Car(self)
        self.ped = Pedestrian(self)
        self.reset_every = self.p.reset_every

    def do_reset(self):
        self.light.reset()
        self.car.reset()
        self.ped.reset()

    def step(self):
        phase = self.light.step()
        self.car.step(phase)
        self.ped.step(phase)
        time.sleep(self.p.step_delay)

        if self.reset_every > 0 and self.t > 0 and self.t % self.reset_every == 0:
            print("[MODEL] RESET programado")
            self.do_reset()

# ============================== MAIN =============================
if __name__ == "__main__":
    params = {
        # Longevidad
        "steps": 9999,

        # Fases del semáforo
        "green_steps": 6,
        "amber_steps": 3,
        "red_steps": 6,
        "min_red_steps": 4,

        # Velocidades
        "car_speed": 1.0,
        "amber_factor": 0.5,
        "ped_speed": 1.0,

        # Visual
        "step_delay": 0.6,

        # Arranque del coche tras verde (retardo opcional)
        "reaction_delay_steps": 1,

        # Cruce peatonal
        "ped_end_z": 6.0,

        # Reinicio automático
        "reset_every": 60
    }
    try:
        open_conn()
        CrossModel(params).run()
    finally:
        close_conn()
