"""
Decision History Logger for Sidra Autonomous Ops
------------------------------------------------
Stores all autonomous decisions for audit and analysis.
"""

import sqlite3
from datetime import datetime
from typing import Dict, List

DB_PATH = "autonomous_history.db"

class DecisionHistory:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self._create_table()

    def _create_table(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node TEXT,
            action TEXT,
            analysis TEXT,
            event_count INTEGER,
            timestamp TEXT
        )
        """)
        self.conn.commit()

    def log_decision(self, node: str, decision: Dict):
        self.conn.execute("""
        INSERT INTO history (node, action, analysis, event_count, timestamp)
        VALUES (?, ?, ?, ?, ?)
        """, (
            node,
            decision.get("action"),
            decision.get("analysis"),
            decision.get("event_count"),
            datetime.utcnow().isoformat()
        ))
        self.conn.commit()

    def get_all(self) -> List[Dict]:
        cursor = self.conn.execute("SELECT * FROM history")
        rows = cursor.fetchall()
        return [dict(id=row[0], node=row[1], action=row[2], analysis=row[3], event_count=row[4], timestamp=row[5]) for row in rows]

# Standalone Test
if __name__ == "__main__":
    dh = DecisionHistory()
    sample_decision = {"action": "MONITOR", "analysis": "Test", "event_count": 1}
    dh.log_decision("server041", sample_decision)
    print(dh.get_all())
