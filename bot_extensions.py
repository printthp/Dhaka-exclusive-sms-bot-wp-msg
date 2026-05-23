def update_system_access(agent_id, level):
    """ড্যাশবোর্ড থেকে এক্সেস চেঞ্জ করার জন্য"""
    conn = sqlite3.connect("bot_v7_ultimate.db")
    c = conn.cursor()
    c.execute("UPDATE agents SET access_level=? WHERE id=?", (level, agent_id))
    conn.commit()
    conn.close()
