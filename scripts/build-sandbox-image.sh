#!/bin/sh

docker build \
    -f packages/akgentic-tool/src/akgentic/tool/sandbox/sandbox.Dockerfile \
    -t akgentic-sandbox:latest .
