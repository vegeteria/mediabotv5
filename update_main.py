import re

with open("media_bot.py", "r") as f:
    content = f.read()

import_str = "from bot.web_server import start_web_server"
new_import = "from bot.web_server import start_web_server\nimport subprocess"

content = content.replace(import_str, new_import)

run_str = """    # Start the web server
    logger.info("Starting web server...")
    await start_web_server()"""

new_run = """    # Start the Rust API Server
    logger.info("Starting MovieBox Rust API Server...")
    import os
    server_dir = os.path.join(os.path.dirname(__file__), "moviebox-server")
    subprocess.Popen(["cargo", "run", "--release"], cwd=server_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Start the web server
    logger.info("Starting web server...")
    await start_web_server()"""

content = content.replace(run_str, new_run)
with open("media_bot.py", "w") as f:
    f.write(content)
print("Patched media_bot.py")
