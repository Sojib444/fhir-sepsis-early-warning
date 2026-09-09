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

await app.RunAsync();

/// <summary>Entry point anchor for integration tests (WebApplicationFactory).</summary>
public partial class Program;