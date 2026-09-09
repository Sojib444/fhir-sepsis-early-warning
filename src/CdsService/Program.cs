using System.Text.Json;
using CdsService;

var builder = WebApplication.CreateBuilder(args);

string hapiBase = builder.Configuration["HAPI_BASE"] ?? "http://localhost:8080/fhir";
string modelBase = builder.Configuration["MODEL_BASE"] ?? "http://localhost:8000";

string loincJson = Path.Combine(AppContext.BaseDirectory, "LoincMap.json");

builder.Services.AddSingleton(new LoincMap(loincJson));
builder.Services.AddHttpClient<HapiSource>(client =>
{
    client.BaseAddress = new Uri(hapiBase);
    client.Timeout = TimeSpan.FromSeconds(30);
});
builder.Services.AddHttpClient<ModelScorer>(client =>
{
    client.BaseAddress = new Uri(modelBase);
    client.Timeout = TimeSpan.FromSeconds(60);
});
builder.Services.AddScoped<IHapiSource>(sp => sp.GetRequiredService<HapiSource>());
builder.Services.AddScoped<IModelScorer>(sp => sp.GetRequiredService<ModelScorer>());
builder.Services.AddScoped<SepsisRiskCardBuilder>();
builder.Services.AddScoped<TrajectoryService>();

var app = builder.Build();
app.UseRouting();

// CDS Hooks discovery (https://cds-hooks.org/specification/current/).
app.MapGet("/cds-services", () => Results.Ok(new
{
    services = new[]
    {
        new
        {
            hook = "patient-view",
            id = "sepsis-risk",
            title = "Sepsis early-warning risk",
            description = "Hourly sepsis risk from FHIR observations (research prototype).",
            prefetch = new
            {
                observations = "Observation?subject={{context.patientId}}&_count=10000&_sort=date",
            },
        },
    },
}));

app.MapPost("/cds-services/sepsis-risk", async (CdsHookRequest request, SepsisRiskCardBuilder builder) =>
{
    string? patientId = request.Context?.PatientId;
    if (string.IsNullOrWhiteSpace(patientId))
    {
        return Results.BadRequest(new { error = "missing context.patientId", cards = Array.Empty<object>() });
    }

    string? prefetchJson = ExtractPrefetchObservations(request);
    CdsCard card = await builder.BuildAsync(patientId, prefetchJson);
    return Results.Ok(new
    {
        cards = new[]
        {
            new
            {
                card.Uuid,
                card.Summary,
                card.Indicator,
                card.Detail,
                source = new { label = card.SourceLabel, url = card.SourceUrl },
                links = Array.Empty<object>(),
            },
        },
    });
});

/// <summary>
/// Prefetch values arrive either as a serialized Bundle object or as a JSON
/// string; normalize both to a string for the parser.
/// </summary>
static string? ExtractPrefetchObservations(CdsHookRequest request)
{
    if (request.Prefetch is null || !request.Prefetch.TryGetValue("observations", out JsonElement value))
    {
        return null;
    }
    return value.ValueKind switch
    {
        JsonValueKind.String => value.GetString(),
        JsonValueKind.Object => value.GetRawText(),
        _ => null,
    };
}

// --- dashboard endpoints (Phase 7) -------------------------------------------

// Patient list with current risk (§13.1). Risk per patient is computed by the
// card path; the list itself is FHIR Patient ids, oldest first.
app.MapGet("/patients", async (int? limit, IHapiSource hapi, IModelScorer model) =>
{
    IReadOnlyList<string> ids = await hapi.ListPatientsAsync(limit ?? 50);
    var assets = new List<object>();
    foreach (string id in ids)
    {
        try
        {
            PatientSnapshot snapshot = await hapi.BuildSnapshotAsync(id, prefetchBundleJson: null);
            FeatureRow row = await model.BuildFeaturesAsync(snapshot);
            ModelResult result = await model.ScoreAsync(row);
            assets.Add(new { patientId = id, result.Risk, result.Threshold, result.Indicator });
        }
        catch (Exception ex) when (ex is System.Net.Http.HttpRequestException or InvalidOperationException)
        {
            // Skip patients the services cannot score; keep the list useful.
        }
    }
    if (assets.Count == 0)
    {
        return Results.UnprocessableEntity(new { error = "no scoreable patients" });
    }
    return Results.Ok(new { patients = assets });
});

// Risk trajectory over time with the true onset hour (§13.2).
app.MapPost("/sepsis-risk/trajectory", async (TrajectoryRequest request, TrajectoryService service) =>
{
    if (string.IsNullOrWhiteSpace(request.PatientId))
    {
        return Results.BadRequest(new { error = "missing patientId" });
    }
    try
    {
        TrajectoryResponse response = await service.BuildAsync(request.PatientId, request.Hour);
        return Results.Ok(response);
    }
    catch (TrajectoryUnavailableException ex)
    {
        return Results.UnprocessableEntity(new { error = ex.Message });
    }
});

// Threshold slider data from the precomputed alert-burden sweep (§13.3).
app.MapGet("/sepsis-risk/sweep", (IConfiguration config) =>
{
    string? path = config["SWEEP_PATH"] ?? Path.Combine(Path.GetFullPath("."), "rigor.json");
    if (!File.Exists(path))
    {
        return Results.NotFound($"sweep file {path} not found; run `make rigor` to produce it");
    }
    using var document = JsonDocument.Parse(File.ReadAllText(path));
    if (!document.RootElement.TryGetProperty("alert_burden", out JsonElement sweep))
    {
        return Results.NotFound("rigor.json has no alert_burden key");
    }
    return Results.Text(sweep.GetRawText(), "application/json");
});

await app.RunAsync();

/// <summary>Entry point anchor for integration tests (WebApplicationFactory).</summary>
public partial class Program;