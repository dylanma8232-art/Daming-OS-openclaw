"""Safe schema and data checks for portable Daming OS memory workspaces."""
from __future__ import annotations
from pathlib import Path
from typing import Dict

class MemoryMigrator:
    def __init__(self, workspace: str): self.root=Path(workspace)
    def verify(self) -> Dict[str,bool]:
        memory=self.root/"memory"
        required=(memory, memory/"hot", memory/"lancedb", self.root/"wiki"/"main")
        return {str(item.relative_to(self.root)):item.exists() for item in required}
    def initialize(self) -> Dict[str,bool]:
        for item in (self.root/"memory"/"hot",self.root/"memory"/"lancedb",self.root/"wiki"/"main",self.root/"memory"/"archive",self.root/"memory"/"health-reports"): item.mkdir(parents=True,exist_ok=True)
        return self.verify()
