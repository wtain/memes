freeze:
	pip freeze > requirements.txt

clean-pycache:
	powershell -NoProfile -Command "Get-ChildItem -Recurse -Filter __pycache__ -Directory | Where-Object { $$_.FullName -notlike '*\.venv*' } | Remove-Item -Recurse -Force"