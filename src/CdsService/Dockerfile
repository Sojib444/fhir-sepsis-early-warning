# Build context is the repository root (docker-compose sets it). Base images
# are pinned by digest (§23), multi-arch manifest lists.
FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:4ea6fe75dd36706bb6d8c3c293d4c4315840f5d76ea28ac97def77e3ec487fa5 AS build
WORKDIR /src

# Restore fast-path: bring in just the project files first.
COPY src/CdsService/CdsService.csproj src/CdsService/
COPY src/FhirLoader/LoincMap.json src/FhirLoader/
RUN dotnet restore src/CdsService/CdsService.csproj

COPY src/ src/
RUN dotnet publish src/CdsService/CdsService.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0@sha256:1fe86375600b62e6566b465da9553eef0621f13c67f40fe764cd8dbb1dee1497
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app .

# Frozen sweep data for the dashboard's threshold slider (§13.3). The
# committed results/rigor.json is an empty placeholder until `make rigor` runs;
# the deploy workflow bakes the real one produced on the tag runner.
COPY results/rigor.json /app/rigor.json

# 8990 keeps clear of HAPI (8080) and the model service (8000).
ENV ASPNETCORE_URLS=http://+:8990 \
    HAPI_BASE=http://hapi:8080/fhir \
    MODEL_BASE=http://model-api:8000
EXPOSE 8990
ENTRYPOINT ["dotnet", "CdsService.dll"]