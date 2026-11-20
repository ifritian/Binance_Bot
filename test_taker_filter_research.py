import taker_filter_research as tf

H = 3600_000


def test_z_before_uses_only_closed_hours():
    hours = [0, H, 2 * H]
    z = {0: 0.1, H: 0.5, 2 * H: 9.9}
    assert tf.z_before(hours, z, 2 * H) == 0.5          # час [1ч,2ч) закрылся ровно к сигналу в 2ч, час 2ч ещё не начался
    assert tf.z_before(hours, z, 2 * H - 1) == 0.1      # час [1ч,2ч) ещё идёт
    assert tf.z_before(hours, z, 10) is None


def test_side_z_flips_for_shorts_and_buckets():
    assert tf.side_z({"direction": "short"}, 1.2) == -1.2 and tf.side_z({"direction": "long"}, None) is None
    res = [{"direction": "long", "side_z": 1.5, "net": 1}, {"direction": "long", "side_z": -0.2, "net": -1}, {"direction": "short", "side_z": None, "net": 0}]
    b = tf.buckets(res)
    assert len(b["все сделки (с данными потока)"]) == 2 and len(b["поток согласен сильно (z>=1)"]) == 1 and len(b["поток против (z<0)"]) == 1
