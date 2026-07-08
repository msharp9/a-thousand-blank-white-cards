from main import main


def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert captured.out == "Hello from a-thousand-blank-white-cards!\n"
