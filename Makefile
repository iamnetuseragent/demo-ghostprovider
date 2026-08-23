# Rust rewrite of demo-ghostprovider
.PHONY: build release test clippy fmt check clean

build:
	cargo build

release:
	cargo build --release

test:
	cargo test

clippy:
	cargo clippy --all-targets -- -D warnings

fmt:
	cargo fmt

check: fmt clippy test

clean:
	cargo clean
