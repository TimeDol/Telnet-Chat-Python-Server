#!/usr/bin/env python3
# bots_simulator.py
# Simulateur de bots pour ton chat LAN (connecte N bots, actions aléatoires)
# Usage: python3 bots_simulator.py --host 192.168.1.15 --port 2323 --bots 20 --rate 3

import socket
import threading
import time
import random
import argparse
import sys

# --- Configuration par défaut ---
SAMPLE_MESSAGES = [
    "Salut tout le monde !",
    "Quelqu'un veut jouer plus tard ?",
    "Je teste le serveur.",
    "Nice !",
    "Qui est admin ici ?",
    "Bug? Je vois rien.",
    "Je dois filer dans 5 min.",
    "C'est chaud aujourd'hui.",
    "Quel est le mot de passe ? (blague)",
    "Héhé :D"
]

SAMPLE_ACTIONS = [
    "message",      # envoie un message normal
    "pm",           # envoie un private message via /msg
    "me",           # /me action
    "dnd_toggle",   # active / désactive DND
    "history",      # demande l'historique
    "clear",        # /clear
    "quit_random",  # quitte proprement
    "disconnect",   # coupe la connexion brutalement
]

# --- Fonction utilitaires bots ---
def rand_nick():
    return f"Bot{random.randint(1000,9999)}"

def safe_recv(sock, timeout=2):
    sock.settimeout(timeout)
    try:
        data = sock.recv(4096)
        return data.decode("utf-8", errors="ignore")
    except socket.timeout:
        return ""
    except Exception:
        return ""

def send_line(sock, line):
    try:
        sock.sendall((line + "\r\n").encode("utf-8", errors="ignore"))
    except Exception:
        raise

# --- Classe BotThread ---
class BotThread(threading.Thread):
    def __init__(self, id_num, host, port, rate, verbose=False, simulate_unstable=False):
        super().__init__(daemon=True)
        self.id_num = id_num
        self.host = host
        self.port = port
        self.rate = rate        # moyenne d'intervalle entre actions (en secondes)
        self.verbose = verbose
        self.simulate_unstable = simulate_unstable
        self.sock = None
        self.name = rand_nick()
        self.alive = True

    def log(self, *args):
        if self.verbose:
            print(f"[{self.name}]", *args)

    def connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((self.host, self.port))
        self.sock = s
        # read welcome prompt (may contain "Choose a nickname:")
        pre = safe_recv(self.sock, timeout=1)
        if self.verbose:
            self.log("recv:", pre.strip())
        # send nick (no Telnet negotiations assumed - use Raw mode)
        send_line(self.sock, self.name)
        # read welcome message
        safe_recv(self.sock, timeout=1)

    def disconnect_clean(self):
        try:
            send_line(self.sock, "/quit")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.alive = False
        self.log("disconnected cleanly")

    def disconnect_dirty(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.alive = False
        self.log("disconnected dirty (abrupt)")

    def do_random_action(self):
        action = random.choices(
            SAMPLE_ACTIONS,
            weights=[60, 10, 8, 6, 4, 4, 4, 4],  # adjust probabilities
            k=1
        )[0]

        if action == "message":
            msg = random.choice(SAMPLE_MESSAGES)
            send_line(self.sock, msg)
            self.log("sent message:", msg)

        elif action == "pm":
            # pick a random target (simple: try BotXXXX or 'User' sample)
            target = f"Bot{random.randint(1000,9999)}"
            text = "[PM] " + random.choice(SAMPLE_MESSAGES)
            send_line(self.sock, f"/msg {target} {text}")
            self.log("sent pm to", target)

        elif action == "me":
            text = "does a random action"
            send_line(self.sock, f"/me {text}")
            self.log("did /me")

        elif action == "dnd_toggle":
            # toggle on/off randomly
            mode = random.choice(["on", "off"])
            send_line(self.sock, f"/dnd {mode}")
            self.log("set dnd", mode)

        elif action == "history":
            send_line(self.sock, "/history")
            # server will ask how many
            time.sleep(0.2)
            amount = random.choice(["10", "30", str(random.randint(5,50))])
            send_line(self.sock, amount)
            # read some response
            safe_recv(self.sock, timeout=0.5)
            self.log("requested history", amount)

        elif action == "clear":
            send_line(self.sock, "/clear")
            self.log("sent /clear")

        elif action == "quit_random":
            send_line(self.sock, "/quit")
            self.log("quit by command")
            self.alive = False

        elif action == "disconnect":
            # abrupt disconnect half the time
            if random.random() < 0.5:
                self.disconnect_dirty()
            else:
                self.disconnect_clean()

    def run(self):
        try:
            self.connect()
        except Exception as e:
            self.log("connect failed:", e)
            return

        # main loop: perform actions until alive False
        while self.alive:
            try:
                # sometimes read server messages to keep socket active
                _ = safe_recv(self.sock, timeout=0.1)
            except Exception:
                pass

            wait = random.expovariate(1.0 / max(0.1, self.rate))
            time.sleep(wait)

            # small chance to simulate unstable network
            if self.simulate_unstable and random.random() < 0.02:
                self.disconnect_dirty()
                break

            try:
                self.do_random_action()
            except Exception as e:
                self.log("action failed:", e)
                # on failure, try to close and exit
                try:
                    self.sock.close()
                except:
                    pass
                break

        # ensure socket closed
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.log("thread exiting")

# --- Orchestrateur pour N bots ---
def spawn_bots(host, port, bots, rate, verbose=False, unstable=False, stagger=0.05):
    threads = []
    for i in range(bots):
        t = BotThread(i, host, port, rate, verbose=verbose, simulate_unstable=unstable)
        t.start()
        threads.append(t)
        time.sleep(stagger)  # small stagger to avoid connection storm
    return threads

# --- CLI et run ---
def main():
    parser = argparse.ArgumentParser(description="Spawn simulated bots for LAN telnet chat.")
    parser.add_argument("--host", required=True, help="Chat server host/IP")
    parser.add_argument("--port", type=int, default=2323, help="Chat server port")
    parser.add_argument("--bots", type=int, default=10, help="Number of bots to spawn")
    parser.add_argument("--rate", type=float, default=5.0, help="Average seconds between bot actions")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds to keep bots alive (0 = indefinite)")
    parser.add_argument("--verbose", action="store_true", help="Print bot actions locally")
    parser.add_argument("--unstable", action="store_true", help="Simulate occasional abrupt disconnects")
    args = parser.parse_args()

    print(f"Spawning {args.bots} bots -> {args.host}:{args.port} (rate ~{args.rate}s).")
    threads = spawn_bots(args.host, args.port, args.bots, args.rate, verbose=args.verbose, unstable=args.unstable)

    start = time.time()
    try:
        while True:
            alive = [t for t in threads if t.is_alive()]
            print(f"[ORCH] Alive bots: {len(alive)}/{len(threads)}", end="\r")
            time.sleep(1)
            if args.duration > 0 and (time.time() - start) > args.duration:
                print("\n[ORCH] Duration reached, asking bots to quit gracefully...")
                for t in threads:
                    try:
                        if t.sock:
                            send_line(t.sock, "/quit")
                    except Exception:
                        pass
                break
            # if all died, stop
            if not alive:
                break
    except KeyboardInterrupt:
        print("\n[ORCH] Interrupted, attempting to stop bots...")
        for t in threads:
            try:
                if t.sock:
                    send_line(t.sock, "/quit")
            except:
                pass

    # wait small time for threads to finish
    time.sleep(2)
    print("\n[ORCH] Done.")

if __name__ == "__main__":
    main()
