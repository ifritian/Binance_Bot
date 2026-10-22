import format_stats as fs


def test_summarize_groups_by_format_and_image(tmp_path):
    log = [{"id": "1", "fmt": "digest", "img": True}, {"id": "2", "fmt": "digest", "img": False},
           {"id": "3", "fmt": "opinion", "img": True}, {"id": "4", "fmt": "opinion", "img": True}]
    csvp = tmp_path / "m.csv"
    csvp.write_text("post_id,views,likes,comments,clicks\n1,100,2,1,\n2,10,0,0,\n3,40,0,0,3\n4,,,,\n")
    rows = fs.summarize(log, fs.load_metrics(str(csvp)))
    by = {r["group"]: r for r in rows}
    assert by["fmt:digest"]["posts"] == 2 and by["fmt:digest"]["median_views"] == 55
    assert by["fmt:opinion"]["posts"] == 1 and by["fmt:opinion"]["clicks"] == 3   # пост без views пропущен
    assert by["image"]["posts"] == 2 and by["no image"]["posts"] == 1
    assert fs.load_metrics(str(tmp_path / "none.csv")) == {}
