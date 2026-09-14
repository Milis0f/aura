from pathlib import Path

from aura.services import storage
from aura.services.security import LAN, LOCAL, REMOTE, zone

# The NAS Mac mini: internal disk with EFI + root + swap, a USB drive whose data partition OpenMediaVault mounts,
# a second unmounted NTFS partition on the same drive, and a USB key formatted as a whole disk.
LSBLK = {
    "blockdevices": [
        {
            "name": "sda", "path": "/dev/sda", "type": "disk", "size": 500107862016, "fstype": None, "label": None,
            "partlabel": None, "uuid": None, "mountpoint": None, "mountpoints": [None], "hotplug": False, "rm": False,
            "tran": "sata", "model": "APPLE HDD", "ro": False, "pkname": None,
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "type": "part", "size": 536870912, "fstype": "vfat", "uuid": "67E8-28B6", "mountpoints": ["/boot/efi"], "hotplug": False, "rm": False},
                {"name": "sda2", "path": "/dev/sda2", "type": "part", "size": 480000000000, "fstype": "ext4", "uuid": "b573a393", "mountpoints": ["/"], "hotplug": False, "rm": False},
                {"name": "sda3", "path": "/dev/sda3", "type": "part", "size": 2000000000, "fstype": "swap", "uuid": "56996039", "mountpoints": ["[SWAP]"]},
            ],
        },
        {
            "name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 3000592982016, "hotplug": True, "rm": False, "tran": "usb", "model": "Expansion HDD",
            "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "size": 536870912, "fstype": "vfat", "uuid": "1017-15DD", "mountpoints": [None], "hotplug": True},
                {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "size": 2990000000000, "fstype": "ntfs", "label": "Nouveau nom", "partlabel": "Basic data partition", "uuid": "8A681C62681C4F77", "mountpoints": ["/srv/dev-disk-by-uuid-8A681C62681C4F77"], "hotplug": True},
                {"name": "sdb3", "path": "/dev/sdb3", "type": "part", "size": 10522669056, "fstype": "ntfs", "label": "linux", "uuid": "6442F75442F72A06", "mountpoints": [None], "hotplug": True},
            ],
        },
        {"name": "sdc", "path": "/dev/sdc", "type": "disk", "size": 64000000000, "fstype": "exfat", "label": "CLE", "uuid": "AAAA-BBBB", "mountpoint": None, "hotplug": "1", "rm": "1", "tran": "usb", "model": "SanDisk"},
    ]
}


def _volumes():
    return {v.device: v for v in storage.parse_lsblk(LSBLK)}


def test_lsblk_keeps_filesystems_and_flags_the_system():
    vols = _volumes()
    assert set(vols) == {"/dev/sda1", "/dev/sda2", "/dev/sdb1", "/dev/sdb2", "/dev/sdb3", "/dev/sdc"}  # swap hidden
    assert vols["/dev/sda1"].system and vols["/dev/sda2"].system and vols["/dev/sdb1"].system
    data = vols["/dev/sdb2"]
    assert (data.label, data.id, data.removable, data.managed, data.parent) == ("Nouveau nom", "uuid:8A681C62681C4F77", True, "omv", "/dev/sdb")
    assert vols["/dev/sdb1"].label == "Expansion HDD 512 Mo"


def test_only_unmounted_removable_data_partitions_are_automounted():
    vols = _volumes()
    assert [d for d, v in vols.items() if storage.should_automount(v)] == ["/dev/sdb3", "/dev/sdc"]


def test_library_volumes_skip_system_and_nested_folders():
    vols = list(_volumes().values())
    inside = storage.Volume(id="dir:a", label="Media", kind="folder", mountpoint="/srv/dev-disk-by-uuid-8A681C62681C4F77/Media")
    elsewhere = storage.Volume(id="dir:b", label="Films", kind="folder", mountpoint="/home/tv/Films")
    nested = storage.Volume(id="dir:c", label="4K", kind="folder", mountpoint="/home/tv/Films/4K")
    chosen = [v.id for v in storage.library_volumes([*vols, inside, elsewhere, nested])]
    assert chosen == ["uuid:8A681C62681C4F77", "dir:b"]


def test_old_lsblk_without_mountpoints_column():
    data = {"blockdevices": [{"name": "sdd1", "path": "/dev/sdd1", "type": "part", "size": 8 * 1024**3, "fstype": "ext4", "uuid": "u1", "mountpoint": "/media/aura/Films", "hotplug": "1", "rm": "0"}]}
    (vol,) = storage.parse_lsblk(data)
    assert vol.mounted and vol.removable and vol.mountpoint == "/media/aura/Films"


def test_human_sizes_are_french():
    assert storage.human_size(2990000000000) == "2,7 To"
    assert storage.human_size(512) == "512 o"


def test_folder_volumes(tmp_path):
    vol = storage.folder_volume(str(tmp_path), "Vidéos")
    assert vol.kind == "folder" and vol.label == "Vidéos" and Path(vol.mountpoint) == tmp_path.resolve()
    assert storage.folder_volume(str(tmp_path / "absent")) is None


async def test_eject_refuses_drives_owned_by_openmediavault():
    ok, message = await storage.eject(_volumes()["/dev/sdb2"], [])
    assert not ok and "OpenMediaVault" in message


def test_zones():
    assert zone("127.0.0.1", {}) == LOCAL
    assert zone("::1", {}) == LOCAL
    assert zone("192.168.1.30", {}) == LAN
    assert zone("10.8.0.2", {}) == LAN  # WireGuard
    assert zone("100.101.102.103", {}) == LAN  # Tailscale
    assert zone("fe80::1", {}) == LAN
    assert zone("::ffff:192.168.1.5", {}) == LAN
    assert zone("8.8.8.8", {}) == REMOTE
    assert zone("127.0.0.1", {"x-forwarded-for": "192.168.1.30"}) == REMOTE  # relayed by nginx
    assert zone(None, {}) == REMOTE
