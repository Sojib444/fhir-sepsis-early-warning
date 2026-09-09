using System.Net;
using System.Text;
using System.Text.Json;
using CdsService;

namespace CdsService.Tests;

public class ModelClientTests
{
    private const string FeaturesJson = """
        {"hour": 17, "features": {"Lactate_mean_24": 2.1, "HR_min_6": null}}
        """;

    private const string PredictJson = """
        {"risk": 0.71, "threshold": 0.14, "indicator": "warning",
         "top_shap": [{"feature": "Lactate_last_24", "value": 0.05},
                      {"feature": "HR_max_6", "value": -0.02}]}
        """;

    private static ModelClient NewClient() => new(
        StubRoutes.NewClient(("/features", FeaturesJson), ("/predict", PredictJson)));

    [Fact]
    public async Task BuildFeatures_parses_row_and_null_cells()
    {
        var client = NewClient();
        var snapshot = new PatientSnapshot("p1",
            [new ObservationRow(0, "HR", 80.0), new ObservationRow(1, "HR", 90.0)],
            Age: 61.0, Gender: 0, Iculos: 2);

        FeatureRow row = await client.BuildFeaturesAsync(snapshot);

        Assert.Equal(17, row.Hour);
        Assert.Equal(2.1, row.Features["Lactate_mean_24"]);
        Assert.Null(row.Features["HR_min_6"]);
    }

    [Fact]
    public async Task Score_parses_result()
    {
        var client = NewClient();
        var row = new FeatureRow(new Dictionary<string, double?>
        {
            ["Lactate_mean_24"] = 2.1,
            ["HR_min_6"] = null,
        }, Hour: 17);

        ModelResult result = await client.ScoreAsync(row);

        Assert.Equal(0.71, result.Risk, 3);
        Assert.Equal(0.14, result.Threshold, 3);
        Assert.Equal("warning", result.Indicator);
        Assert.Equal("Lactate_last_24", result.TopShap[0].Feature);
        Assert.Single(result.TopShap, s => s.Value < 0);
    }

    [Fact]
    public async Task Non_success_response_is_an_exception()
    {
        var client = new ModelClient(StubRoutes.NewClient());
        var snapshot = new PatientSnapshot("p1", [new ObservationRow(0, "HR", 80.0)], null, null, 1);
        await Assert.ThrowsAsync<HttpRequestException>(() => client.BuildFeaturesAsync(snapshot));
    }
}