import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from opendance.game import named_pose
from tools.reprocess_songs import refresh_manifest


class ReprocessSongsTest(unittest.TestCase):
    def test_stale_role_timeline_is_backed_up_refreshed_and_then_current(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "song.json"
            song = {
                "choreography": {
                    "dancers": [{"index": 0, "track_id": 7}],
                    "timeline": [
                        {
                            "timestamp_ms": index * 250,
                            "people": [{
                                "dancer_index": 0,
                                "keypoints": named_pose("ready" if index < 4 else "star"),
                            }],
                        }
                        for index in range(9)
                    ],
                }
            }
            path.write_text(json.dumps(song), encoding="utf-8")
            original = json.loads(path.read_text())

            self.assertTrue(refresh_manifest(path))
            self.assertEqual(
                json.loads(path.with_name("song.json.bak").read_text()), original
            )
            self.assertTrue(json.loads(path.read_text())["choreography"]["move_scoring"]["segments"])
            self.assertFalse(refresh_manifest(path))


if __name__ == "__main__":
    unittest.main()
