# Build context is the repository root (docker-compose sets it).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

# Restore fast-path: bring in just the project files first.
COPY src/CdsService/CdsService.csproj src/CdsService/
COPY src/FhirLoader/LoincMap.json src/FhirLoader/
RUN dotnet restore src/CdsService/CdsService.csproj

COPY src/ src/
RUN dotnet publish src/CdsService/CdsService.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app .

# 8990 keeps clear of HAPI (8080) and the model service (8000).
ENV ASPNETCORE_URLS=http://+:8990 \
    HAPI_BASE=http://hapi:8080/fhir \
    MODEL_BASE=http://model-api:8000
EXPOSE 8990
ENTRYPOINT ["dotnet", "CdsService.dll"]