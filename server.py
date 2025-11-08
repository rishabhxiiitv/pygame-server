import asyncio
import json
import random
import websockets
import os
import time
import datetime # NEW: For timestamps

# Constants
WIDTH, HEIGHT = 800, 600
RESOURCE_SPAWN_TIME = 5
PLAYER_SIZE = 64
RESOURCE_SIZE = 32

# --- Server State ---
players = {}
resources = []
next_player_id = 1
next_resource_id = 1
clients = {}
LOBBY_PASSWORD = ""
game_state = "lobby"
host_player_id = 0
game_start_time = 0
game_end_time = 0
STATE_LOCK = asyncio.Lock()

# --- Broadcast function (Unchanged) ---
async def broadcast_updates():
    if not clients:
        return
    
    update = json.dumps({
        "type": "update",
        "players": players,
        "resources": resources,
        "game_state": game_state,
        "host_player_id": host_player_id,
        "game_end_time": game_end_time
    })
    
    for client_websocket in list(clients.values()):
        try:
            await client_websocket.send(update)
        except websockets.exceptions.ConnectionClosed:
            pass

# --- NEW: Chat Broadcast Function (With Timestamps) ---
async def broadcast_chat_message(sender_id, sender_name, sender_color, message):
    """Broadcasts a single chat message to all connected clients."""
    if not clients:
        return
        
    # NEW: Get a timestamp
    now = datetime.datetime.now()
    timestamp = now.strftime("%I:%M %p") # e.g., "02:45 PM"

    chat_payload = json.dumps({
        "type": "chat_broadcast",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_color": sender_color,
        "message": message,
        "timestamp": timestamp      # --- NEW ---
    })
    
    for client_websocket in list(clients.values()):
        try:
            await client_websocket.send(chat_payload)
        except websockets.exceptions.ConnectionClosed:
            pass # Client will be removed by the handler

# --- NEW: System Message Broadcast Function ---
async def broadcast_system_message(message):
    """Broadcasts a join/leave message to all connected clients."""
    if not clients:
        return
        
    now = datetime.datetime.now()
    timestamp = now.strftime("%I:%M %p")

    chat_payload = json.dumps({
        "type": "system_message",
        "message": message,
        "timestamp": timestamp
    })
    
    for client_websocket in list(clients.values()):
        try:
            await client_websocket.send(chat_payload)
        except websockets.exceptions.ConnectionClosed:
            pass

# --- NEW: Game End Timer ---
async def end_game_timer(seconds_to_wait):
    """
    Waits for the game to end, moves to leaderboard for 10s,
    then disconnects all clients and resets the server.
    """
    global game_state, host_player_id, next_player_id
    
    # 1. Wait for game duration
    await asyncio.sleep(seconds_to_wait)
    
    # 2. Move to Leaderboard state
    await STATE_LOCK.acquire()
    try:
        if game_state == "playing":
            print("--- GAME TIMER ENDED. Moving to leaderboard. ---")
            game_state = "leaderboard"
            resources.clear()
    finally:
        STATE_LOCK.release()
        
    await broadcast_updates() # Tell all clients to show leaderboard

    # 3. Wait 10 seconds for leaderboard view
    await asyncio.sleep(10)
    
    # 4. Disconnect all clients and reset server
    await STATE_LOCK.acquire()
    try:
        print("--- Leaderboard time over. Disconnecting all clients. ---")
        for client_id, client_websocket in list(clients.items()):
            try:
                # Code 1000 = Normal Closure
                await client_websocket.close(code=1000, reason="Game Over")
            except websockets.exceptions.ConnectionClosed:
                pass # Already disconnected
        
        # Reset server state for the next lobby
        players.clear()
        clients.clear()
        resources.clear()
        game_state = "lobby"
        host_player_id = 0
        next_player_id = 1 # Reset player IDs
        
    finally:
        STATE_LOCK.release()

# --- Client Handler (MODIFIED) ---
async def handle_client(websocket):
    global next_player_id, host_player_id, game_state, game_end_time, game_start_time
    player_id = 0
    player_name = ""

    try:
        # 1. Handle Join
        join_message = await websocket.recv()
        join_data = json.loads(join_message)

        if join_data.get("type") == "join" and join_data.get("password") == LOBBY_PASSWORD:
            await STATE_LOCK.acquire()
            try:
                # --- Prevent joining a game in progress ---
                if game_state != "lobby":
                    await websocket.send(json.dumps({"type": "join_fail", "reason": "Game is already in progress."}))
                    await websocket.close()
                    return
                
                player_id = next_player_id
                next_player_id += 1
                player_name = join_data.get("name", f"Player{player_id}")
                
                r, g, b = random.randint(100, 255), random.randint(100, 255), random.randint(100, 255)
                player_color = join_data.get("color", (r,g,b))

                players[player_id] = {
                    "x": random.randint(PLAYER_SIZE, WIDTH - PLAYER_SIZE),
                    "y": random.randint(PLAYER_SIZE, HEIGHT - PLAYER_SIZE),
                    "score": 0, "name": player_name, "color": player_color
                }
                clients[player_id] = websocket

                if len(players) == 1:
                    host_player_id = player_id
                print(f"Player {player_id} ({player_name}) has joined.")
            finally:
                STATE_LOCK.release()
            
            await websocket.send(json.dumps({"type": "join_success", "player_id": player_id}))
            await broadcast_updates()
            # --- NEW: Send system message on join ---
            await broadcast_system_message(f"{player_name} has joined.")
        else:
            await websocket.send(json.dumps({"type": "join_fail", "reason": "Wrong password"}))
            await websocket.close()
            return

        # 2. Handle Message Loop
        async for message in websocket:
            data = json.loads(message)
            broadcast_needed = False

            # --- NEW: Handle Chat Message ---
            if data["type"] == "chat":
                if player_id in players:
                    message_text = data.get("message", "").strip()
                    if message_text: # Don't broadcast empty messages
                        sender_data = players[player_id]
                        print(f"[CHAT] {sender_data['name']}: {message_text}")
                        # Broadcast to all clients
                        await broadcast_chat_message(
                            player_id,
                            sender_data["name"],
                            sender_data["color"],
                            message_text
                        )
                continue # Chat message doesn't need a state lock
            # --- END OF NEW CHAT LOGIC ---

            await STATE_LOCK.acquire()
            try:
                if data["type"] == "move":
                    if game_state == "playing" and player_id in players:
                        # ... (All your move/collision logic) ...
                        player = players[player_id]
                        old_x, old_y = player["x"], player["y"]
                        potential_x = old_x + data["dx"]
                        collided_player_id_x = None
                        for other_id, other_player in players.items():
                            if other_id == player_id: continue
                            if abs(potential_x - other_player["x"]) < PLAYER_SIZE and abs(old_y - other_player["y"]) < PLAYER_SIZE:
                                collided_player_id_x = other_id
                                break
                        if collided_player_id_x is not None:
                            target_player = players[collided_player_id_x]
                            target_potential_x = target_player["x"] + data["dx"]
                            is_target_x_blocked = False
                            if not (PLAYER_SIZE // 2 <= target_potential_x <= WIDTH - PLAYER_SIZE // 2): is_target_x_blocked = True
                            if not is_target_x_blocked:
                                for p3_id, p3_player in players.items():
                                    if p3_id == player_id or p3_id == collided_player_id_x: continue
                                    if abs(target_potential_x - p3_player["x"]) < PLAYER_SIZE and abs(target_player["y"] - p3_player["y"]) < PLAYER_SIZE:
                                        is_target_x_blocked = True
                                        break
                            if not is_target_x_blocked:
                                target_player["x"] = target_potential_x
                                player["x"] = potential_x
                        else:
                            player["x"] = potential_x
                        potential_y = old_y + data["dy"]
                        collided_player_id_y = None
                        for other_id, other_player in players.items():
                            if other_id == player_id: continue
                            if abs(player["x"] - other_player["x"]) < PLAYER_SIZE and abs(potential_y - other_player["y"]) < PLAYER_SIZE:
                                collided_player_id_y = other_id
                                break
                        if collided_player_id_y is not None:
                            target_player = players[collided_player_id_y]
                            target_potential_y = target_player["y"] + data["dy"]
                            is_target_y_blocked = False
                            if not (PLAYER_SIZE // 2 <= target_potential_y <= HEIGHT - PLAYER_SIZE // 2): is_target_y_blocked = True
                            if not is_target_y_blocked:
                                for p3_id, p3_player in players.items():
                                    if p3_id == player_id or p3_id == collided_player_id_y: continue
                                    if abs(target_player["x"] - p3_player["x"]) < PLAYER_SIZE and abs(target_potential_y - p3_player["y"]) < PLAYER_SIZE:
                                        is_target_y_blocked = True
                                        break
                            if not is_target_y_blocked:
                                target_player["y"] = target_potential_y
                                player["y"] = potential_y
                        else:
                            player["y"] = potential_y
                        for p in players.values():
                            p["x"] = max(PLAYER_SIZE // 2, min(WIDTH - PLAYER_SIZE // 2, p["x"]))
                            p["y"] = max(PLAYER_SIZE // 2, min(HEIGHT - PLAYER_SIZE // 2, p["y"]))
                        for resource in resources[:]:
                            if abs(player["x"] - resource["x"]) < PLAYER_SIZE / 2 and abs(player["y"] - resource["y"]) < PLAYER_SIZE / 2:
                                resources.remove(resource)
                                player["score"] += 1
                        broadcast_needed = True
                
                elif data["type"] == "start_game":
                    if player_id == host_player_id and game_state == "lobby":
                        duration_minutes = data.get("duration", 2)
                        duration_seconds = duration_minutes * 60
                        print(f"--- Game Started by Host (Player {player_id}) for {duration_minutes} minutes ---")
                        game_state = "playing"
                        game_start_time = time.time()
                        game_end_time = game_start_time + duration_seconds
                        asyncio.create_task(end_game_timer(duration_seconds))
                        broadcast_needed = True
                        
            finally:
                STATE_LOCK.release()
            
            if broadcast_needed:
                await broadcast_updates()

    except websockets.exceptions.ConnectionClosed:
        print(f"Connection closed for Player {player_id}.")
    finally:
        # 3. Handle Disconnect
        broadcast_needed = False
        await STATE_LOCK.acquire()
        try:
            if player_id in players:
                print(f"Player {player_id} ({player_name}) has disconnected.")
                del players[player_id]
                if player_id in clients:
                    del clients[player_id]
                
                # Promote new host if the host left (at any stage)
                if player_id == host_player_id:
                    if players: 
                        new_host_id = min(players.keys())
                        host_player_id = new_host_id
                        print(f"Host disconnected. Promoting Player {new_host_id} to new host.")
                    else: 
                        host_player_id = 0
                        print("Last player left. Resetting host.")
                
                # If last player leaves, reset the server
                if not players:
                    print("All players disconnected. Resetting server.")
                    game_state = "lobby"
                    host_player_id = 0
                    next_player_id = 1
                    resources.clear()
                
                broadcast_needed = True # Need to tell others about the disconnect
        finally:
            STATE_LOCK.release()
        
        if broadcast_needed: 
            await broadcast_updates()
            # --- NEW: Send system message on leave ---
            await broadcast_system_message(f"{player_name} has left.")

# --- Resource Spawner (Unchanged) ---
async def spawn_resources():
    global next_resource_id
    while True:
        await asyncio.sleep(RESOURCE_SPAWN_TIME)
        broadcast_needed = False
        if clients:
            await STATE_LOCK.acquire()
            try:
                if game_state == "playing":
                    resources.append({
                        "id": next_resource_id,
                        "x": random.randint(RESOURCE_SIZE, WIDTH - RESOURCE_SIZE),
                        "y": random.randint(RESOURCE_SIZE, HEIGHT - RESOURCE_SIZE)
                    })
                    next_resource_id += 1
                    broadcast_needed = True
            finally:
                STATE_LOCK.release()
        if broadcast_needed:
            await broadcast_updates()

# --- Main Server Function (Unchanged) ---
async def server_main():
    global LOBBY_PASSWORD
    LOBBY_PASSWORD = os.environ.get("LOBBY_PASSWORD", "default_pass_123")
    print(f"Lobby password is set.")
    port = int(os.environ.get("PORT", 8765))
    
    async with websockets.serve(handle_client, "0.0.0.0", port, ping_interval=20, ping_timeout=20):
        print(f"Server started at ws://0.0.0.0:{port}")
        await asyncio.Future()

async def main():
    await asyncio.gather(
        spawn_resources(),
        server_main()
    )

if __name__ == "__main__":
    asyncio.run(main())
