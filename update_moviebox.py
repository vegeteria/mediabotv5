import re

with open("bot/commands/moviebox.py", "r") as f:
    content = f.read()

old_block = """    venv_moviebox = str(Path(__file__).parent.parent.parent / ".venv" / "bin" / "moviebox")
    if not os.path.exists(venv_moviebox):
        venv_moviebox = shutil.which("moviebox") or "moviebox"
    cmd = [venv_moviebox, "v3"]
    
    unique_id = str(uuid.uuid4())[:8]
    target_dir = BASE_MOVIES / unique_id if state["type"] == "movie" else BASE_SERIES / unique_id
    target_dir.mkdir(parents=True, exist_ok=True)

    cmds = []
    
    if state["type"] == "movie":
        cmd.extend([
            "download-movie", title,
            "--quality", quality,
            "--dir", str(target_dir),
            "--caption-dir", str(target_dir),
            "--part-dir", str(target_dir),
            "--yes", "--tasks", "5",
            "--ignore-missing-caption",
        ])
        if state.get("year"):
            cmd.extend(["--year", str(state["year"])])
        if dub and dub.lower() != "original audio":
            cmd.extend(["--dub", dub])
        cmds.append(cmd)
    else:
        base_series_cmd = cmd + [
            "download-series", title,
            "--quality", quality,
            "--dir", str(target_dir),
            "--caption-dir", str(target_dir),
            "--part-dir", str(target_dir),
            "--yes", "--tasks", "5",
            "--format", "struct",
            "--ignore-missing-caption",
        ]
        if dub and dub.lower() != "original audio":
            base_series_cmd.extend(["--dub", dub])
            
        s = state.get("season", 1)
            
        if state.get("scope") == "auto":
            cmds.append(base_series_cmd + ["-s", "1", "-e", "1", "--auto-mode"])
        elif state.get("scope") == "season":
            total_eps = state.get("total_episodes", 100)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", "1", "--limit", str(total_eps)])
        elif state.get("scope") == "range":
            e = state.get("episode", 1)
            limit = state.get("episode_limit", 1)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", str(e), "--limit", str(limit)])
        elif state.get("scope") == "selected":
            # Group into contiguous blocks to minimize CLI calls
            selected = sorted(list(state.get("selected_episodes", set())))
            if not selected:
                await query.message.edit_text("❌ No episodes were selected.")
                return
                
            blocks = []
            current_start = selected[0]
            current_count = 1
            
            for i in range(1, len(selected)):
                if selected[i] == selected[i-1] + 1:
                    current_count += 1
                else:
                    blocks.append((current_start, current_count))
                    current_start = selected[i]
                    current_count = 1
            blocks.append((current_start, current_count))
            
            for block_start, block_limit in blocks:
                cmds.append(base_series_cmd + ["-s", str(s), "-e", str(block_start), "--limit", str(block_limit)])
        else:
            e = state.get("episode", 1)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", str(e), "--limit", "1"])"""

new_block = """    python_bin = str(Path(__file__).parent.parent.parent / ".venv" / "bin" / "python")
    if not os.path.exists(python_bin):
        import sys
        python_bin = sys.executable
        
    script_path = str(Path(__file__).parent.parent / "rust_moviebox.py")
    cmd = [python_bin, script_path, "--id", str(state.get("search_id")), "--title", title]
    
    unique_id = str(uuid.uuid4())[:8]
    target_dir = BASE_MOVIES / unique_id if state["type"] == "movie" else BASE_SERIES / unique_id
    target_dir.mkdir(parents=True, exist_ok=True)

    cmds = []
    
    if state["type"] == "movie":
        cmds.append(cmd + ["--dir", str(target_dir)])
    else:
        s = state.get("season", 1)
        base_series_cmd = cmd + ["--dir", str(target_dir), "--season", str(s)]
            
        if state.get("scope") == "auto":
            cmds.append(base_series_cmd + ["--episode", "1", "--limit", "1"])
        elif state.get("scope") == "season":
            total_eps = state.get("total_episodes", 100)
            cmds.append(base_series_cmd + ["--episode", "1", "--limit", str(total_eps)])
        elif state.get("scope") == "range":
            e = state.get("episode", 1)
            limit = state.get("episode_limit", 1)
            cmds.append(base_series_cmd + ["--episode", str(e), "--limit", str(limit)])
        elif state.get("scope") == "selected":
            selected = sorted(list(state.get("selected_episodes", set())))
            if not selected:
                await query.message.edit_text("❌ No episodes were selected.")
                return
                
            blocks = []
            current_start = selected[0]
            current_count = 1
            for i in range(1, len(selected)):
                if selected[i] == selected[i-1] + 1:
                    current_count += 1
                else:
                    blocks.append((current_start, current_count))
                    current_start = selected[i]
                    current_count = 1
            blocks.append((current_start, current_count))
            
            for block_start, block_limit in blocks:
                cmds.append(base_series_cmd + ["--episode", str(block_start), "--limit", str(block_limit)])
        else:
            e = state.get("episode", 1)
            cmds.append(base_series_cmd + ["--episode", str(e), "--limit", "1"])"""

content = content.replace(old_block, new_block)
with open("bot/commands/moviebox.py", "w") as f:
    f.write(content)
print("Patched moviebox.py")
