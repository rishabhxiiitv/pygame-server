import asyncio
import json
import random
import websockets
import os
import asyncio # Make sure this is imported
import time

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
STATE_LOCK = asyncio.Lock() # <-- NEW: The master lock
game_start_time = 0
game_end_time = 0

# --- Broadcast function (Safer) ---
async def broadcast_updates():
    if not clients:
        return
    
    # We must send a custom message to each client
    # This JSON dump is now safe because it's only called from a locked state
    update = json.dumps({
        "type": "update",
        "players": players,
        "resources": resources,
        "game_state": game_state,
        "host_player_id": host_player_id,
        "game_end_time": game_end_time  # <-- ADD THIS LINE
    })
    # Loop over a copy of the list, in case a client
    # disconnects *during* the broadcast
    for client_websocket in list(clients.values()):
        try:
            await client_websocket.send(update)
        except websockets.exceptions.ConnectionClosed:
            pass

# --- Client Handler (Now uses the lock) ---
async def handle_client(websocket):
    global next_player_id, host_player_id, game_state
    player_id = 0
    player_name = ""

    try:
        # 1. Handle Join
        join_message = await websocket.recv()
        join_data = json.loads(join_message)

        if join_data.get("type") == "join" and join_data.get("password") == LOBBY_PASSWORD:
            
            # --- LOCK STATE FOR JOINING ---
            await STATE_LOCK.acquire()
            try:
                player_id = next_player_id
                next_player_id += 1
                player_name = join_data.get("name", f"Player{player_id}")
                
                player_color = join_data.get("color") 
                if player_color is None:
                    r, g, b = random.randint(100, 255), random.randint(100, 255), random.randint(100, 255)
                    player_color = (r, g, b)

                players[player_id] = {
                    "x": random.randint(PLAYER_SIZE, WIDTH - PLAYER_SIZE),
                    "y": random.randint(PLAYER_SIZE, HEIGHT - PLAYER_SIZE),
                    "score": 0, "name": player_name, "color": player_color
                }
                clients[player_id] = websocket 

                if len(players) == 1:
                    host_player_id = player_id
                    print(f"Player {player_id} is now the host.")

                print(f"Player {player_id} ({player_name}) has joined.")
            finally:
                STATE_LOCK.release() # <-- Release lock
            
            # --- Send messages *after* releasing lock ---
            await websocket.send(json.dumps({"type": "join_success", "player_id": player_id}))
            await broadcast_updates()

        else:
            await websocket.send(json.dumps({"type": "join_fail", "reason": "Wrong password"}))
            await websocket.close()
            return

        # 2. Handle Message Loop
        async for message in websocket:
            data = json.loads(message)
            broadcast_needed = False

            # --- LOCK STATE FOR ALL IN-GAME ACTIONS ---
            await STATE_LOCK.acquire()
            try:
                if data["type"] == "move":
                    if game_state == "playing" and player_id in players:
                        # ... (All your move/collision logic can go here) ...
                        player = players[player_id]
                        old_x, old_y = player["x"], player["y"]

                        # 1. X-AXIS
                        potential_x = old_x + data["dx"]
                        collided_player_id_x = None
                        for other_id, other_player in players.items():
                            if other_id == player_id: continue
                            if abs(potential_x - other_player["x"]) < PLAYER_SIZE and \
                               abs(old_y - other_player["y"]) < PLAYER_SIZE:
                                collided_player_id_x = other_id
                                break
                        
                        if collided_player_id_x is not None:
                            target_player = players[collided_player_id_x]
                            target_potential_x = target_player["x"] + data["dx"]
                            is_target_x_blocked = False
                            if not (PLAYER_SIZE // 2 <= target_potential_x <= WIDTH - PLAYER_SIZE // 2):
                                is_target_x_blocked = True
                            if not is_target_x_blocked:
                                for p3_id, p3_player in players.items():
                                    if p3_id == player_id or p3_id == collided_player_id_x: continue
                                    if abs(target_potential_x - p3_player["x"]) < PLAYER_SIZE and \
                                       abs(target_player["y"] - p3_player["y"]) < PLAYER_SIZE:
                                        is_target_x_blocked = True
                                        break
                            if not is_target_x_blocked:
                                target_player["x"] = target_potential_x
                                player["x"] = potential_x
                        else:
                            player["x"] = potential_x

                        # 2. Y-AXIS
                        potential_y = old_y + data["dy"]
                        collided_player_id_y = None
                        for other_id, other_player in players.items():
                            if other_id == player_id: continue
                            if abs(player["x"] - other_player["x"]) < PLAYER_SIZE and \
                               abs(potential_y - other_player["y"]) < PLAYER_SIZE:
                                collided_player_id_y = other_id
                                break
                        if collided_player_id_y is not None:
                            target_player = players[collided_player_id_y]
                            target_potential_y = target_player["y"] + data["dy"]
                            is_target_y_blocked = False
                            if not (PLAYER_SIZE // 2 <= target_potential_y <= HEIGHT - PLAYER_SIZE // 2):
                                is_target_y_blocked = True
                            if not is_target_y_blocked:
                                for p3_id, p3_player in players.items():
                                    if p3_id == player_id or p3_id == collided_player_id_y: continue
                                    if abs(target_player["x"] - p3_player["x"]) < PLAYER_SIZE and \
                                       abs(target_potential_y - p3_player["y"]) < PLAYER_SIZE:
                                        is_target_y_blocked = True
                                        break
                            if not is_target_y_blocked:
                                target_player["y"] = target_potential_y
                                player["y"] = potential_y
                        else:
                            player["y"] = potential_y

                        # Clamp all player positions
                        for p in players.values():
                            p["x"] = max(PLAYER_SIZE // 2, min(WIDTH - PLAYER_SIZE // 2, p["x"]))
                            p["y"] = max(PLAYER_SIZE // 2, min(HEIGHT - PLAYER_SIZE // 2, p["y"]))

                        # Check for resource collection
                        for resource in resources[:]:
                            if abs(player["x"] - resource["x"]) < PLAYER_SIZE / 2 and \
                               abs(player["y"] - resource["y"]) < PLAYER_SIZE / 2:
                                resources.remove(resource)
                                player["score"] += 1
                        
                        broadcast_needed = True
                
                elif data["type"] == "start_game":
                    global game_end_time, game_start_time
                    if player_id == host_player_id and game_state == "lobby":
                        
                        # Get duration from client, default to 2 mins
                        duration_minutes = data.get("duration", 2)
                        duration_seconds = duration_minutes * 60
                        
                        print(f"--- Game Started by Host (Player {player_id}) for {duration_minutes} minutes ---")

                        game_state = "playing"
                        game_start_time = time.time()
                        game_end_time = game_start_time + duration_seconds
                        
                        # Start the background timer task
                        asyncio.create_task(end_game_timer(duration_seconds))
                        
                        broadcast_needed = True
                elif data["type"] == "reset_game":
                    # Only the host can reset, and only from the leaderboard screen
                    if player_id == host_player_id and game_state == "leaderboard":
                        print(f"--- Host (Player {player_id}) is resetting game ---")
                        game_state = "lobby"
                        broadcast_needed = True
                        
            finally:
                STATE_LOCK.release() # <-- Release lock
            
            # Broadcast *after* releasing lock
            if broadcast_needed:
                await broadcast_updates()

    except websockets.exceptions.ConnectionClosed:
        pass # Client disconnected
    finally:
        # 3. Handle Disconnect
        # --- LOCK STATE FOR DISCONNECTING ---
        await STATE_LOCK.acquire()
        try:
            if player_id in players:
                print(f"Player {player_id} ({player_name}) has disconnected.")
                del players[player_id]
                if player_id in clients:
                    del clients[player_id] 
                
                # Promote a new host if the host left
                if player_id == host_player_id and game_state == "lobby":
                    if players: 
                        new_host_id = min(players.keys())
                        host_player_id = new_host_id
                        print(f"Host disconnected. Promoting Player {new_host_id} to new host.")
                    else: 
                        host_player_id = 0
                        print("Last player left. Resetting host.")
        finally:
            STATE_LOCK.release() # <-- Release lock
        
        # Broadcast the disconnect *after* releasing the lock
        if player_id != 0: # Only broadcast if the player actually joined
            await broadcast_updates()

# --- Resource Spawner (Now uses the lock) ---
async def spawn_resources():
    global next_resource_id
    while True:
        await asyncio.sleep(RESOURCE_SPAWN_TIME)
        
        broadcast_needed = False
        if clients:
            # --- LOCK STATE FOR SPAWNING ---
            await STATE_LOCK.acquire()
            try:
                # Only spawn resources if the game is playing
                if game_state == "playing":
                    resources.append({
                        "id": next_resource_id,
                        "x": random.randint(RESOURCE_SIZE, WIDTH - RESOURCE_SIZE),
                        "y": random.randint(RESOURCE_SIZE, HEIGHT - RESOURCE_SIZE)
                    })
                    next_resource_id += 1
                    broadcast_needed = True
            finally:
                STATE_LOCK.release() # <-- Release lock

        if broadcast_needed:
            await broadcast_updates()


async def failsafe_reset_timer():
    """
    A 30-second failsafe in case the host disconnects 
    from the leaderboard screen.
    """
    global game_state
    await asyncio.sleep(30) # Wait 30 seconds
    
    await STATE_LOCK.acquire()
    try:
        # If we are still on the leaderboard after 30s,
        # force a reset back to the lobby.
        if game_state == "leaderboard":
            print("--- Failsafe Timer: Returning to lobby ---")
            game_state = "lobby"
            # Broadcast this change
            await broadcast_updates() 
    finally:
        STATE_LOCK.release()

# --- Main Server Function (Unchanged from Render version) ---
async def server_main():
    global LOBBY_PASSWORD
    LOBBY_PASSWORD = os.environ.get("LOBBY_PASSWORD", "default_pass_123")
    print(f"Lobby password is set.")
    port = int(os.environ.get("PORT", 8765))
    
    async with websockets.serve(handle_client, "0.0.0.0", port):
        print(f"Server started at ws://0.0.0.0:{port}")
        await asyncio.Future()

async def end_game_timer(seconds_to_wait):
    """
    A background task that waits for the game to end,
    then moves the state to the leaderboard.
    """
    global game_state
    
    await asyncio.sleep(seconds_to_wait)
    
    await STATE_LOCK.acquire()
    try:
        if game_state == "playing":
            print("--- GAME TIMER ENDED. Moving to leaderboard. ---")
            game_state = "leaderboard" # <-- MODIFIED
            resources.clear() 
    finally:
        STATE_LOCK.release()
        
    # Tell all clients to go to the leaderboard
    await broadcast_updates()
    
    # Start the 30-second failsafe timer
    asyncio.create_task(failsafe_reset_timer())


async def main():
    await asyncio.gather(
        spawn_resources(),
        server_main()
    )

if __name__ == "__main__":
    asyncio.run(main())

