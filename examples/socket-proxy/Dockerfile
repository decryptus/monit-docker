FROM alpine@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS build
RUN apk add --no-cache go
WORKDIR /src
COPY main.go .
RUN CGO_ENABLED=0 go build -trimpath -o /socket-proxy main.go

FROM scratch
COPY --from=build /socket-proxy /socket-proxy
USER 65534:65534
ENTRYPOINT ["/socket-proxy"]
