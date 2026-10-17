"""Chat backup: export, merge into another install, restore identity."""

import json
import zipfile

import pytest

from muninn import backup
from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import wait_until


def _history(lab):
    a = lab.node("a", (0, 0))
    b = lab.node("b", (1, 0))
    c = lab.node("c", (0, 1))
    wait_until(lambda: len(a.mesh.connected()) == 2, 30)
    a.mesh.send_text(dm_conv(b.node_id), "hello b")
    gid = a.mesh.create_group("trip", [b.node_id, c.node_id])
    wait_until(lambda: gid in b.mesh.groups, 20)
    b.mesh.send_text(group_conv(gid), "hi room")
    wait_until(
        lambda: any(
            m.text == "hi room" for m in a.storage.load_history(group_conv(gid))
        ),
        20,
    )
    return a, b, c, gid


def test_backup_restores_identity_and_history_on_a_fresh_install(lab, tmp_path):
    a, b, c, gid = _history(lab)
    f = tmp_path / "f.txt"
    f.write_text("boarding pass")
    a.mesh.send_file(dm_conv(b.node_id), str(f), "text/plain")
    zpath = tmp_path / "a.zip"
    counts = a.export_backup(str(zpath), password="hunter2")
    assert counts["messages"] >= 4 and counts["files"] == 1
    with zipfile.ZipFile(zpath) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert "privkey" not in json.dumps(manifest)  # sealed, not plain
        assert any(n.endswith("/f.txt") for n in z.namelist())
    with pytest.raises(ValueError):
        backup.import_(
            lab.node("fresh-wrong", (9, 9), start=False).storage, zpath, "nope"
        )

    new = lab.node("newphone", (0, 2), start=False)
    res = new.import_backup(str(zpath), password="hunter2")
    assert res["identity"] is True
    assert new.storage.get_identity().node_id == a.node_id
    texts = [m.text for m in new.storage.load_history(dm_conv(b.node_id))]
    assert "hello b" in texts
    assert "hi room" in [m.text for m in new.storage.load_history(group_conv(gid))]
    rec = next(r for r in new.storage.load_files() if r.name == "f.txt")
    assert open(rec.path).read() == "boarding pass" and rec.state == "complete"
    assert gid in new.mesh.groups and b.node_id in new.mesh.peers


def test_backup_merges_back_into_the_same_person_without_duplicates(lab, tmp_path):
    a, b, c, gid = _history(lab)
    zpath = tmp_path / "a.zip"
    a.export_backup(str(zpath))  # no password: history only
    assert sum(a.import_backup(str(zpath))["added"].values()) == 0  # nothing new
    n = len(a.storage.load_history(group_conv(gid)))
    a.mesh.delete_conversation(dm_conv(b.node_id))  # oops
    res = a.import_backup(str(zpath))
    assert res["identity"] is False and res["added"]["messages"] >= 1
    assert "hello b" in [m.text for m in a.storage.load_history(dm_conv(b.node_id))]
    assert len(a.storage.load_history(group_conv(gid))) == n  # no duplicates


def test_someone_elses_backup_is_refused(lab, tmp_path):
    a, b, c, gid = _history(lab)
    zpath = tmp_path / "a.zip"
    a.export_backup(str(zpath), password="x")
    with pytest.raises(ValueError):
        b.import_backup(str(zpath))  # history from a's point of view
    with pytest.raises(ValueError):
        b.import_backup(str(zpath), password="x")  # b already has chats
    assert b.storage.get_identity().node_id == b.node_id
