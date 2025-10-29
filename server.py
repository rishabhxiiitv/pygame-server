import asyncio
import json
import random
import websockets
import os  # <-- THIS IMPORT IS NEW

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
game_state = "lobby" # <-- ADD THIS LINE

# --- MODIFIED: Clients is now a dictionary {player_id: websocket} ---
clients = {} 
LOBBY_PASSWORD = "" # This will be set when the server starts

# --- NEW: Broadcast function with visibility logic ---
async def broadcast_updates():
    if not clients: # Don't do anything if no one is connected
        return
    
    # --- NEW, SIMPLER LOGIC ---
    # Construct one update message with ALL player data
    update = json.dumps({
        "type": "update",
        "players": players, # Send the FULL, unfiltered player list
        "resources": resources,
        "game_state": game_state,
        "host_player_id": host_player_id
    })
    
    # Send the same update to everyone
    for client_websocket in clients.values():
        try:
            await client_websocket.send(update)
        except websockets.exceptions.ConnectionClosed:
            pass


async def handle_client(websocket):
    global next_player_id
    player_id = 0 # We'll assign this *after* they join
    player_name = ""

    try:
        # --- Wait for the client to send their "join" message first
        join_message = await websocket.recv()
        join_data = json.loads(join_message)

        # --- Check password ---
        if join_data.get("type") == "join" and join_data.get("password") == LOBBY_PASSWORD:
            player_id = next_player_id
            next_player_id += 1
            player_name = join_data.get("name", f"Player{player_id}")
            
            player_color = join_data.get("color") 
            if player_color is None:
                r = random.randint(100, 255)
                g = random.randint(100, 255)
                b = random.randint(100, 255)
                player_color = (r, g, b)

            players[player_id] = {
                "x": random.randint(PLAYER_SIZE, WIDTH - PLAYER_SIZE),
                "y": random.randint(PLAYER_SIZE, HEIGHT - PLAYER_SIZE),
                "score": 0,
                "name": player_name,
                "color": player_color
            }
            # --- MODIFIED: Add to dict instead of set ---
            clients[player_id] = websocket 
            print(f"Player {player_id} ({player_name}) has joined with color {player_color}.")
            
            await websocket.send(json.dumps({"type": "join_success", "player_id": player_id}))
            
            # --- MODIFIED: Send first update using new broadcast ---
            await broadcast_updates()

        else:
            print(f"Failed login attempt with password: {join_data.get('password')}")
            await websocket.send(json.dumps({"type": "join_fail", "reason": "Wrong password"}))
            await websocket.close()
            return

        # --- This loop now only handles "move" messages
        async for message in websocket:
            data = json.loads(message)

            if data["type"] == "move":
                # --- NEW: Only allow movement if the game is playing ---
                if game_state != "playing":
                    continue
                    
                if player_id not in players:
                    continue

                # --- PUSHING COLLISION LOGIC (Unchanged) ---
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
                # --- END OF PUSH LOGIC ---

                # Clamp all player positions
                for p_id, p in players.items():
                    p["x"] = max(PLAYER_SIZE // 2, min(WIDTH - PLAYER_SIZE // 2, p["x"]))
                    p["y"] = max(PLAYER_SIZE // 2, min(HEIGHT - PLAYER_SIZE // 2, p["y"]))

                # Check for resource collection
                for resource in resources[:]:
                    if abs(player["x"] - resource["x"]) < PLAYER_SIZE / 2 and \
                       abs(player["y"] - resource["y"]) < PLAYER_SIZE / 2:
                        resources.remove(resource)
                        player["score"] += 1

                # --- MODIFIED: Broadcast using the new function ---
                await broadcast_updates()
                
            elif data["type"] == "start_game":
                # Only Player 1 (the host) can start the game
                if player_id == 1 and game_state == "lobby":
                    game_state = "playing"
                    print("--- Game Started by Host (Player 1) ---")
                    # Broadcast the new "playing" state to all clients
                    await broadcast_updates()

    except websockets.exceptions.ConnectionClosed:
        pass # Client disconnected
    finally:
        # Clean up if the player was successfully added
        if player_id in players:
            print(f"Player {player_id} ({player_name}) has disconnected.")
            del players[player_id]
            # --- MODIFIED: Remove from dict ---
            if player_id in clients:
                del clients[player_id] 
            
            # --- MODIFIED: Broadcast disconnect using new function ---
            await broadcast_updates()


async def spawn_resources():
    global next_resource_id
    while True:
        await asyncio.sleep(RESOURCE_SPAWN_TIME)
        
        if clients:
            resources.append({
                "id": next_resource_id,
                "x": random.randint(RESOURCE_SIZE, WIDTH - RESOURCE_SIZE),
                "y": random.randint(RESOURCE_SIZE, HEIGHT - RESOURCE_SIZE)
            })
            next_resource_id += 1

            # --- MODIFIED: Broadcast using the new function ---
            await broadcast_updates()

#
# --- 🚀 THIS IS THE PART THAT WAS MODIFIED FOR RENDER 🚀 ---
#
async def server_main():
    global LOBBY_PASSWORD
    
    # Get the password from a secure "Environment Variable" on Render
    LOBBY_PASSWORD = os.environ.get("LOBBY_PASSWORD", "default_pass_123")
    print(f"Lobby password is set.")

    # Get the port from Render. Render tells us which port to use.
    port = int(os.environ.get("PORT", 8765))
    
    async with websockets.serve(handle_client, "0.0.0.0", port):
        print(f"Server started at ws://0.0.0.0:{port}")
        await asyncio.Future()  # keep running
#
# --- END OF MODIFIED SECTION ---
#

async def main():
    await asyncio.gather(
        spawn_resources(),
        server_main()
    )

if __name__ == "__main__":
    asyncio.run(main())



