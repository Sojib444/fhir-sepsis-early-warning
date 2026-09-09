using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;
using CdsService;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace CdsService.Tests;

/// <summary>
/// Exercises the real ASP.NET routing and card serialization (Program.cs) with
/// the backend services replaced by doubles, so no HAPI or model container is
/// needed. The full HAPI -> CDS -> model journey is covered by the docker smoke
/// script (scripts/cds_demo.sh) in CI.
/// </summary>
public class CdsEndpointTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public CdsEndpointTests(WebApplicationFactory<Program> factory) => _factory = factory;

    private static readonly PatientSnapshot Snapshot = new(
        "p1",
        [new ObservationRow(0, "HR", 80.0), new ObservationRow(1, "Lactate", 2.1)],
        Age: 61.0, Gender: 0, Iculos: 2);

    private static readonly FeatureRow Features = new(
        new Dictionary<string, double?> { ["Lactate_mean_24"] = 2.1, ["HR_min_6"] = null }, Hour: 1);

    private static readonly ModelResult Result = new(
        Risk: 0.71, Threshold: 0.14, Indicator: "warning",
        TopShap: [new ShapContribution("Lactate_last_24", 0.05)]);

    private WebApplicationFactory<Program> WithFakes() =>
        _factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
        {
            services.RemoveAll<IHapiSource>();
            services.RemoveAll<IModelScorer>();
            services.AddScoped<IHapiSource>(_ => new FakeHapiSource(Snapshot));
            services.AddScoped<IModelScorer>(_ => new FakeModelScorer(Features, Result));
        }));

    [Fact]
    public async Task Discovery_document_lists_the_hook()
    {
        using HttpClient client = _factory.CreateClient();

        using HttpResponseMessage http = await client.GetAsync("/cds-services");
        using JsonDocument doc = await JsonDocument.ParseAsync(await http.Content.ReadAsStreamAsync());
        JsonElement first = doc.RootElement.GetProperty("services")[0];
        Assert.Equal("patient-view", first.GetProperty("hook").GetString());
        Assert.Equal("sepsis-risk", first.GetProperty("id").GetString());
    }

    [Theory]
    [InlineData("string")]
    [InlineData("object")]
    public async Task Card_round_trip_prefetch_as(string prefetchKind)
    {
        using HttpClient client = WithFakes().CreateClient();

        string bundleJson = TestHelpers.PrefetchJson("p1", 1, (1, "HR", 80m), (2, "Lactate", 2.1m));
        JsonNode? prefetchValue = prefetchKind == "string"
            ? JsonValue.Create(bundleJson)
            : JsonNode.Parse(bundleJson);

        var payload = new JsonObject
        {
            ["hook"] = "patient-view",
            ["context"] = new JsonObject { ["patientId"] = "p1" },
            ["prefetch"] = new JsonObject { ["observations"] = prefetchValue },
        };

        HttpResponseMessage response = await client.PostAsJsonAsync("/cds-services/sepsis-risk", payload);
        Assert.Equal(System.Net.HttpStatusCode.OK, response.StatusCode);

        using JsonDocument doc = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
        JsonElement card = doc.RootElement.GetProperty("cards")[0];
        Assert.StartsWith("Sepsis risk 71.0%", card.GetProperty("summary").GetString());
        Assert.Equal("warning", card.GetProperty("indicator").GetString());
        Assert.Contains("Research prototype", card.GetProperty("detail").GetString());
        Assert.Equal("Sepsis early-warning (research prototype)",
            card.GetProperty("source").GetProperty("label").GetString());
    }

    [Fact]
    public async Task Missing_patient_id_is_400()
    {
        using HttpClient client = _factory.CreateClient();
        var payload = new JsonObject
        {
            ["hook"] = "patient-view",
            ["context"] = new JsonObject { },
        };
        HttpResponseMessage response = await client.PostAsJsonAsync("/cds-services/sepsis-risk", payload);
        Assert.Equal(System.Net.HttpStatusCode.BadRequest, response.StatusCode);
    }
}