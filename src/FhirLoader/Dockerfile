# Build context is the repository root (docker-compose / the publish job).
# Base images are pinned by digest (§23), multi-arch manifest lists.
FROM mcr.microsoft.com/dotnet/sdk:10.0@sha256:4ea6fe75dd36706bb6d8c3c293d4c4315840f5d76ea28ac97def77e3ec487fa5 AS build
WORKDIR /src

# Restore fast-path: bring in just the project files first.
COPY src/FhirLoader/FhirLoader.csproj src/FhirLoader/
COPY src/FhirLoader/LoincMap.json src/FhirLoader/
RUN dotnet restore src/FhirLoader/FhirLoader.csproj

COPY src/ src/
RUN dotnet publish src/FhirLoader/FhirLoader.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/runtime:10.0@sha256:399e54a8a7e35c3aba78398b2840455d45185cba20b831b8a2b46f849f4f5001
WORKDIR /app
COPY --from=build /app .

# The demo seed (a small subset of the Challenge 2019 data, permitted by its
# data use terms) is baked in by the publish job. data/ itself stays out of git.
COPY deploy/loader-assets/fhirloader.csv /data/fhirloader.csv

ENTRYPOINT ["dotnet", "FhirLoader.dll"]