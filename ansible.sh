#!/bin/bash
docker run -it --network ansible --add-host=host.docker.internal:host-gateway -v ${pwd}:/apps -w /apps ansible $@