using System;
using System.Net; using System.Net.Sockets;
using System.Text; using System.Threading;
using System.Collections.Concurrent;
using UnityEngine;

[Serializable] public class Msg {
    public string type;       // "state" o "light"
    public string agent_id;   // "car_1", "pedestrian"
    public float x, z;        // posición
    public string state;      // "verde" / "amarillo" / "rojo"
    public string action;     // "AVANZA"/"ESPERA"/"SIGUE"/"REINICIO"...
    public int step;
}

public class PythonTcpServer : MonoBehaviour {
    TcpListener listener; Thread listenThread; volatile bool running;
    ConcurrentQueue<Msg> queue = new ConcurrentQueue<Msg>();

    void Start() {
        Application.runInBackground = true;
        listener = new TcpListener(IPAddress.Any, 5005);
        listener.Start(); running = true;
        listenThread = new Thread(ListenLoop){ IsBackground = true };
        listenThread.Start();
        Debug.Log("[UNITY] Listening on 5005");
    }

    void ListenLoop() {
        try {
            while (running) {
                var client = listener.AcceptTcpClient();
                new Thread(() => HandleClient(client)){ IsBackground = true }.Start();
            }
        } catch (SocketException) { /* al cerrar */ }
        catch (Exception e) { Debug.LogWarning("[UNITY] ListenLoop error: " + e); }
    }

    void HandleClient(TcpClient client) {
        using (client)
        using (var ns = client.GetStream()) {
            byte[] buf = new byte[4096];
            var sb = new StringBuilder();

            // opcional: saludar
            var hello = Encoding.UTF8.GetBytes("hello from unity\n");
            ns.Write(hello, 0, hello.Length);

            while (running && client.Connected) {
                int n;
                try { n = ns.Read(buf, 0, buf.Length); } catch { break; }
                if (n <= 0) break;

                sb.Append(Encoding.UTF8.GetString(buf, 0, n));

                // Procesar por líneas (cada JSON termina en '\n')
                while (true) {
                    string s = sb.ToString();
                    int idx = s.IndexOf('\n');
                    if (idx < 0) break;

                    string line = s.Substring(0, idx).Trim();
                    sb.Remove(0, idx + 1);
                    if (line.Length == 0) continue;

                    Debug.Log("[UNITY<-PY] " + line);

                    try {
                        var m = JsonUtility.FromJson<Msg>(line);
                        if (m != null) queue.Enqueue(m);
                    } catch (Exception je) {
                        Debug.LogWarning("[UNITY] JSON parse error: " + je.Message);
                    }
                }
            }
        }
        Debug.Log("[UNITY] Client disconnected");
    }

    void Update() {
        // Consumir mensajes en el hilo principal
        while (queue.TryDequeue(out var m)) {
            if (m.type == "state" && !string.IsNullOrEmpty(m.agent_id)) {
                var go = GameObject.Find(m.agent_id);
                if (go != null) {
                    var p = go.transform.position;
                    go.transform.position = new Vector3(m.x, p.y, m.z);
                    if (!string.IsNullOrEmpty(m.action) && m.action == "REINICIO") {
                        Debug.Log($"[UNITY] Reset recibido para {m.agent_id} (step {m.step})");
                    }
                }
            } else if (m.type == "light") {
                var lamp = GameObject.Find("traffic_light");
                if (lamp != null) {
                    var r = lamp.GetComponent<Renderer>();
                    if (r != null) {
                        if (m.state == "GREEN")      r.material.color = Color.green;
                        else if (m.state == "AMBER") r.material.color = Color.yellow;
                        else                          r.material.color = Color.red;
                    }
                }
            }
        }
    }

    void OnApplicationQuit() {
        running = false;
        try { listener?.Stop(); } catch {}
        try { listenThread?.Join(200); } catch {}
    }
}
