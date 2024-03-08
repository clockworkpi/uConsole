#!/bin/bash

# To be able to build kernel modules container needs to run in privileged mode
# [https://projectatomic.io/blog/2018/06/building-kernel-modules-with-podman/]

# Spawn container and run build script
docker run --rm --privileged -v ./:/uConsole ubuntu:22.04 /bin/bash /uConsole/build-kernel.sh

# Start container to run build script manually
# docker run -it --rm --privileged -v ./:/uConsole ubuntu:22.04
