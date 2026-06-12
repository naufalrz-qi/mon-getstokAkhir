import logging
from app.Services.Snapshot.SnapshotCore import SnapshotCore
from app.Services.Snapshot.SnapshotRunner import SnapshotRunner
from app.Services.Snapshot.SnapshotQuery import SnapshotQuery
from app.Services.Snapshot.SnapshotAutoUpdate import SnapshotAutoUpdate
from app.Services.Snapshot.SnapshotState import SnapshotState

class SnapshotManager(SnapshotCore, SnapshotRunner, SnapshotQuery, SnapshotAutoUpdate):
    """Facade for SnapshotManager"""
    pass
