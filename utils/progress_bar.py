def generate_progress(sent, total, length=20):
    filled = int(length * sent / total)
    empty = length - filled
    return f"[{'█'*filled}{'░'*empty}] {sent}/{total}"
