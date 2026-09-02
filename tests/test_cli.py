from lake_cli.cli import parser


def test_parses_ingest_command():
    args = parser().parse_args(["ingest", "example.source", "--force"])
    assert args.command == "ingest"
    assert args.dataset_id == "example.source"
    assert args.force


def test_parses_status_command():
    args = parser().parse_args(["--json", "status", "example.source", "--limit", "5"])
    assert args.json
    assert args.limit == 5


def test_query_preserves_client_arguments():
    args = parser().parse_args(["query", "--", "-c", "SELECT 1"])
    assert args.arguments == ["--", "-c", "SELECT 1"]
