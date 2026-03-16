import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import ContextDB, data_dir, resolve_cluster_db

class TestClusterJoin:
    def test_join_writes_cluster_yaml(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)  # ensure DB exists
        satellite_root = tempfile.mkdtemp()
        satellite_dir = data_dir(satellite_root)
        from lib.cluster import join_cluster
        join_cluster(satellite_dir, master_root, "test-cluster")
        yaml_path = os.path.join(satellite_dir, "cluster.yaml")
        assert os.path.exists(yaml_path)
        content = open(yaml_path).read()
        assert "test-cluster" in content
        assert master_root in content

    def test_join_validates_master_is_git_root(self):
        import pytest
        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster
        non_git_dir = tempfile.mkdtemp()  # real dir, not a git repo
        with pytest.raises(ValueError, match="not a git repository"):
            join_cluster(satellite_dir, non_git_dir, "test")

    def test_join_makes_resolve_work(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)
        satellite_root = tempfile.mkdtemp()
        satellite_dir = data_dir(satellite_root)
        from lib.cluster import join_cluster
        join_cluster(satellite_dir, master_root, "test-cluster")
        result = resolve_cluster_db(satellite_dir)
        assert result == master_dir

class TestClusterShow:
    def test_show_standalone(self, capsys):
        tmp = tempfile.mkdtemp()
        from lib.cluster import show_cluster
        show_cluster(tmp)
        assert "standalone" in capsys.readouterr().out.lower()

    def test_show_with_cluster(self, capsys):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)
        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, show_cluster
        join_cluster(satellite_dir, master_root, "my-cluster")
        show_cluster(satellite_dir)
        out = capsys.readouterr().out
        assert "my-cluster" in out
        assert master_root in out

class TestClusterLeave:
    def test_leave_removes_yaml(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)
        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, leave_cluster
        join_cluster(satellite_dir, master_root, "test")
        leave_cluster(satellite_dir)
        assert not os.path.exists(os.path.join(satellite_dir, "cluster.yaml"))

    def test_leave_when_not_in_cluster(self, capsys):
        tmp = tempfile.mkdtemp()
        from lib.cluster import leave_cluster
        leave_cluster(tmp)
        assert "not in a cluster" in capsys.readouterr().out.lower()

    def test_leave_then_resolve_is_standalone(self):
        master_root = tempfile.mkdtemp()
        master_dir = data_dir(master_root)
        ContextDB(master_dir)
        satellite_dir = tempfile.mkdtemp()
        from lib.cluster import join_cluster, leave_cluster
        join_cluster(satellite_dir, master_root, "test")
        leave_cluster(satellite_dir)
        assert resolve_cluster_db(satellite_dir) == satellite_dir
