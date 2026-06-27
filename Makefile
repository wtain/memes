freeze:
	pip freeze > requirements.txt

clean-pycache:
	find . -type d -name __pycache__ -not -path './.venv*' | xargs rm -rf