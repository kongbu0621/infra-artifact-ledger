"""Raw mountinfo text model; not a real mounted-storage acceptance test."""
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from infra_artifact_ledger import snapshot_sqlite as sqlite
from infra_artifact_ledger import snapshot_storage as storage


class MountTextReviewTests(unittest.TestCase):
    def test_only_literal_lf_terminates_mountinfo_records(self):
        for character in ("\r", "\u0085", "\u00a0", "\u2028"):
            with self.subTest(character=repr(character)):
                point = "/tmp/source" + character + "mount"
                raw = ("901 1 0:1 / / rw - ext4 root rw\n"
                       f"902 901 0:2 / {point} rw - ext4 child rw\n")

                def reader(*args, **kwargs):
                    # Exercise the actual TextIOWrapper newline handling. A
                    # StringIO/mock_open would miss default CR translation.
                    return io.TextIOWrapper(io.BytesIO(raw.encode()), **kwargs)

                with patch.object(storage, "open", side_effect=reader, create=True):
                    mounts = storage._mounts()
                self.assertEqual(len(mounts), 2)
                self.assertEqual(mounts[1].point, point)
                with patch.object(sqlite, "open", side_effect=reader, create=True):
                    bindings = sqlite._mount_bindings(Path(point) / "database.sqlite")
                self.assertEqual(len(bindings), 2)
                self.assertEqual(bindings[1][4], point)


if __name__ == "__main__":
    unittest.main()
