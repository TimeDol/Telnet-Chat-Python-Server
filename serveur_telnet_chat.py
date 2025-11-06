#!/usr/bin/env python3
# chat_server.py
# Version robuste : gestion propre des deconnexions inattendues + features (mentions, history, dnd, etc.)

import socket
import threading
import datetime
import random
import os
import sys
import traceback
import urllib.request
import json

HOST = "0.0.0.0"
PORT = 2323
LOG_FILE = "chat_log.txt"

# ANSI colors (safe for PuTTY raw mode)
COLORS = [
    "\033[91m", "\033[92m", "\033[93m",
    "\033[94m", "\033[95m", "\033[96m"
]
RESET = "\033[0m"
GREEN_HACKER = "\033[92m"
CLEAR_SCREEN = "\033[2J\033[H"
TIME_COLOR = "\033[90m"   # grey for timestamps
SYS_COLOR = "\033[93m"    # yellow for system messages
CMD_COLOR = "\033[95m"    # magenta for commands
ALERT_COLOR = "\033[91m"  # red for mentions/alerts
SERVER_ALERT = "\033[94m" # blue for server notifications
HISTORY_COLOR = "\033[94m"

clients = {}   # socket -> {addr,name,color,dnd}
lock = threading.Lock()

# -------------------- utilitaires --------------------

def timestamp():
    """Return formatted timestamp with color (string, not ending newline)."""
    return f"{TIME_COLOR}[{datetime.datetime.now().strftime('%H:%M:%S')}] {RESET}"

def console_log(msg):
    """Print message to server console and append to log file (no exception escapes)."""
    try:
        print(msg)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            # store plain text without ANSI color codes for easier history reading
            stripped = strip_ansi(msg)
            f.write(stripped + "\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}", file=sys.stderr)

def strip_ansi(s):
    """Rudimentary removal of ANSI sequences for log file readability."""
    # remove ESC [ ... m sequences
    import re
    return re.sub(r'\x1b\[[0-9;]*m', '', s)

def safe_send(conn, text, raw=False):
    """Send text safely to client; if raw True, do not append CRLF."""
    try:
        out = text if raw else text + "\r\n"
        conn.sendall(out.encode("utf-8", errors="ignore"))
    except Exception:
        # on any sending error, disconnect client cleanly
        disconnect_client(conn)

def broadcast(text, sender=None):
    """Broadcast text to all clients except sender. Safe to call while clients may change."""
    with lock:
        for c in list(clients.keys()):
            if c == sender:
                continue
            try:
                safe_send(c, text)
            except Exception:
                # safe_send does disconnect_client already
                pass

def disconnect_client(conn):
    """Remove client from dict and notify others; safe to call multiple times."""
    with lock:
        info = clients.pop(conn, None)
    if info:
        name = info.get("name", "<unknown>")
        try:
            conn.close()
        except:
            pass
        msg = f"{timestamp()}{SYS_COLOR}{name} left the chat.{RESET}"
        broadcast(msg, sender=None)
        console_log(f"{name} disconnected.")
    # else: already removed

# -------------------- client handler --------------------

def handle_client(conn, addr):
    # make this thread resilient; any uncaught exception must be caught here
    try:
        safe_send(conn, "Welcome to the LAN Telnet Chat!\r\nChoose a nickname: ", raw=True)
        try:
            data = conn.recv(1024)
        except Exception:
            disconnect_client(conn)
            return
        if not data:
            disconnect_client(conn)
            return
        nickname = data.decode("utf-8", errors="ignore").strip()
        if not nickname:
            nickname = f"User{random.randint(1000,9999)}"

        with lock:
            existing = [info["name"] for info in clients.values()]
            while nickname in existing:
                safe_send(conn, "Name already taken. Choose another: ", raw=True)
                try:
                    data = conn.recv(1024)
                except Exception:
                    disconnect_client(conn)
                    return
                if not data:
                    disconnect_client(conn)
                    return
                nickname = data.decode("utf-8", errors="ignore").strip()
                if not nickname:
                    nickname = f"User{random.randint(1000,9999)}"

            color = random.choice(COLORS)
            clients[conn] = {
                "addr": addr,
                "name": nickname,
                "color": color,
                "dnd": False
            }

        welcome = f"{timestamp()}{SYS_COLOR}Welcome {color}{nickname}{RESET}{SYS_COLOR}! Type /help for commands.{RESET}"
        safe_send(conn, welcome)
        broadcast(f"{timestamp()}{SYS_COLOR}{nickname} joined the chat.{RESET}", sender=conn)
        console_log(f"{nickname} connected from {addr}")

        # main loop
        while True:
            try:
                data = conn.recv(2048)
            except (ConnectionResetError, BrokenPipeError):
                # client disconnected uncleanly
                break
            except Exception as e:
                console_log(f"[RECV ERROR] {e}")
                break

            if not data:
                # peer closed connection
                break

            try:
                msg = data.decode("utf-8", errors="ignore").strip()
            except Exception:
                # decoding problem: ignore this message
                continue

            if not msg:
                continue

            # command or chat
            if msg.startswith("/"):
                try:
                    handle_command(conn, msg)
                except Exception as e:
                    console_log(f"[CMD ERROR] {e}\n{traceback.format_exc()}")
                    safe_send(conn, "Command processing error.")
            else:
                # normal message
                with lock:
                    info = clients.get(conn)
                    if not info:
                        # client disappeared while processing
                        break
                    nickname = info["name"]
                    color = info["color"]

                formatted = f"{timestamp()}{color}{nickname}{RESET}: {msg}"
                # check mentions (alerts)
                try:
                    check_mentions(conn, msg, formatted)
                except Exception:
                    console_log(f"[MENTION ERROR] {traceback.format_exc()}")
                broadcast(formatted, sender=conn)
                safe_send(conn, formatted)
                console_log(f"{nickname}: {msg}")

    except Exception as e:
        # catch-all for the thread
        console_log(f"[THREAD EXC] {e}\n{traceback.format_exc()}")
    finally:
        disconnect_client(conn)

# -------------------- mentions / alerts --------------------

def check_mentions(sender_conn, msg, formatted):
    """Detect @mentions and alert the target user (with DND support)."""
    with lock:
        # copy items to avoid modification during iteration
        items = list(clients.items())
    sender_name = clients.get(sender_conn, {}).get("name", "<unknown>")
    for c, info in items:
        if c == sender_conn:
            continue
        target_name = info.get("name", "")
        if f"@{target_name}" in msg:
            if info.get("dnd"):
                console_log(f"{SERVER_ALERT}[MENTION] {sender_name} -> {target_name} (ignored, DND active){RESET}")
                continue
            # build alert (BEL + colored text)
            try:
                alert_msg = (
                    f"\a{ALERT_COLOR}[ALERTE]{RESET} "
                    f"{SYS_COLOR}{sender_name} mentioned you!{RESET}\r\n"
                    f"{formatted}"
                )
                safe_send(c, alert_msg)
                console_log(f"{SERVER_ALERT}[MENTION] {sender_name} -> {target_name}{RESET}")
            except Exception:
                # safe_send will handle disconnect
                pass

# -------------------- commands --------------------

def handle_command(conn, msg):
    parts = msg.split(" ", 2)
    cmd = parts[0].lower()
    with lock:
        user_info = clients.get(conn)
    if not user_info:
        return
    name = user_info["name"]
    color = user_info["color"]

    if cmd == "/help":
        help_text = (
            f"\r\n{GREEN_HACKER}Available commands:{RESET}\r\n"
            f"{CMD_COLOR}/help{RESET}    - Show this help message\r\n"
            f"{CMD_COLOR}/users{RESET}   - List connected users\r\n"
            f"{CMD_COLOR}/quit{RESET}    - Leave the chat\r\n"
            f"{CMD_COLOR}/msg {SYS_COLOR}<user> <text>{RESET} - Send private message\r\n"
            f"{CMD_COLOR}/me {SYS_COLOR}<text>{RESET} - Say something in third person\r\n"
            f"{CMD_COLOR}/clear{RESET}   - Clear your screen\r\n"
            f"{CMD_COLOR}/history{RESET} - Reload old messages from logs\r\n"
            f"{CMD_COLOR}/dnd {SYS_COLOR}on{RESET}|{SYS_COLOR}off{RESET} - Toggle Do Not Disturb mode\r\n"
        )
        safe_send(conn, help_text)

    elif cmd == "/users":
        with lock:
            user_list = "\r\n".join(
                [f"- {info['color']}{info['name']}{RESET} {'(DND)' if info.get('dnd') else ''}" for info in clients.values()]
            )
        safe_send(conn, f"\r\n{SYS_COLOR}Connected users:{RESET}\r\n{user_list}")

    elif cmd == "/msg" and len(parts) >= 3:
        target_name, text = parts[1], parts[2]
        target_conn = None
        with lock:
            for c, info in clients.items():
                if info["name"].lower() == target_name.lower():
                    target_conn = c
                    break
        if target_conn:
            private_msg = f"{timestamp()}(private) {color}{name}{RESET}: {text}"
            # beep only if not DND
            if not clients.get(target_conn, {}).get("dnd"):
                safe_send(target_conn, private_msg + "\a", raw=True)
            else:
                safe_send(target_conn, private_msg)
            safe_send(conn, private_msg)
            console_log(f"{SERVER_ALERT}[PM] {name} -> {target_name}{RESET}")
        else:
            safe_send(conn, "User not found.")

    elif cmd == "/me" and len(parts) >= 2:
        action = parts[1]
        msg_out = f"{timestamp()}* {color}{name}{RESET} {action}"
        broadcast(msg_out, sender=conn)
        safe_send(conn, msg_out)
        console_log(f"* {name} {action}")

    elif cmd == "/clear":
        safe_send(conn, CLEAR_SCREEN, raw=True)

    elif cmd == "/history":
        safe_send(conn, "How many messages to show? (10/30/custom): ", raw=True)
        try:
            data = conn.recv(1024)
            if not data:
                return
            amount_data = data.decode("utf-8", errors="ignore").strip()
            if amount_data.isdigit():
                amount = int(amount_data)
            elif amount_data == "10":
                amount = 10
            elif amount_data == "30":
                amount = 30
            else:
                amount = 10
            show_history(conn, amount)
        except Exception as e:
            safe_send(conn, f"Error reading logs: {e}")

    elif cmd == "/dnd" and len(parts) >= 2:
        mode = parts[1].lower()
        with lock:
            if mode == "on":
                clients[conn]["dnd"] = True
                safe_send(conn, f"{SYS_COLOR}Do Not Disturb mode enabled.{RESET}")
                console_log(f"{SERVER_ALERT}[DND] {name} activated DND mode{RESET}")
            elif mode == "off":
                clients[conn]["dnd"] = False
                safe_send(conn, f"{SYS_COLOR}Do Not Disturb mode disabled.{RESET}")
                console_log(f"{SERVER_ALERT}[DND] {name} disabled DND mode{RESET}")
            else:
                safe_send(conn, "Usage: /dnd on|off")

    elif cmd == "/quit":
        safe_send(conn, "Goodbye!")
        disconnect_client(conn)

    else:
        safe_send(conn, "Unknown command. Type /help for list.")

# -------------------- history --------------------

def show_history(conn, amount):
    if not os.path.exists(LOG_FILE):
        safe_send(conn, "No log file found.")
        return
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # get last 'amount' non-empty lines
        last_lines = [l.rstrip("\n") for l in lines if l.strip()][-amount:]
        safe_send(conn, f"\r\n{HISTORY_COLOR}--- Last {len(last_lines)} messages ---{RESET}")
        for line in last_lines:
            # send raw line (no extra color to keep history readable)
            safe_send(conn, line)
        safe_send(conn, f"{HISTORY_COLOR}--- End of history ---{RESET}")
    except Exception as e:
        safe_send(conn, f"Error loading logs: {e}")

# -------------------- server start --------------------

def start_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # optionally enable keepalive (helps detect dead peers)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        server.bind((HOST, PORT))
        server.listen(100)
        console_log(f"{SERVER_ALERT}Server started on {HOST}:{PORT}{RESET}")
        while True:
            try:
                conn, addr = server.accept()
                client_ip = addr[0]
                # Autoriser IP locales
                if client_ip.startswith(("127.", "192.168.", "10.", "172.")):
                    pass
                else:
                    try:
                        with urllib.request.urlopen(f"https://ipapi.co/{client_ip}/json/") as resp:
                            data = json.load(resp)
                            country = data.get("country")
                            if country != "CH":
                                console_log(f"Blocked non-Swiss IP: {client_ip} ({country})")
                                conn.close()
                                continue
                    except Exception as e:
                        console_log(f"GeoIP check failed for {client_ip}: {e}")
                        conn.close()
                        continue
                # make recv non-blocking? we keep blocking but thread cleans itself
                t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
                t.start()
            except KeyboardInterrupt:
                console_log("Server shutting down (KeyboardInterrupt).")
                break
            except Exception as e:
                console_log(f"[ACCEPT ERROR] {e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    start_server()
