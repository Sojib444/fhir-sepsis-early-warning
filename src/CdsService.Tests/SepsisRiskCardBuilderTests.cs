using System.Globalization;
using System.Net;
using CdsService;

namespace CdsService.Tests;

public class SepsisRiskCardBuilderTests
{
    private static readonly PatientSnapshot Snapshot = new(
        "p1",
        [new ObservationRow(0, "HR", 80.0), new ObservationRow(1, "Lactate", 2.1)],
        Age: 61.0, Gender: 0, Iculos: 2);

    private static readonly FeatureRow Features =
        new(new Dictionary<string, double?> { ["Lactate_mean_24"] = 2.1, ["HR_min_6"] = null }, Hour: 1);

    private static readonly ModelResult AboveThreshold = new(
        Risk: 0.71, Threshold: 0.14, Indicator: "warning",
        TopShap:
        [
            new ShapContribution("Lactate_last_24", 0.05),
            new ShapContribution("HR_max_6", -0.02),
            new ShapContribution("O2Sat_min_6", 0.01),
            new ShapContribution("SBP_slope_24", 0.0),
        ]);

    [Fact]
    public async Task Happy_path_card_contains_risk_threshold_and_top_factors()
    {
        var builder = new SepsisRiskCardBuilder(
            new FakeHapiSource(Snapshot),
            new FakeModelScorer(features: Features, result: AboveThreshold));

        CdsCard card = await builder.BuildAsync("p1", prefetchBundleJson: null);

        Assert.StartsWith("Sepsis risk 71.0%", card.Summary);
        Assert.Equal("warning", card.Indicator);
        Assert.Contains("operating threshold (14.0%)", card.Detail);
        Assert.Contains("Research prototype", card.Detail);
        Assert.Contains("Top contributing factors: Lactate_last_24 (+0.05)", card.Detail);
        Assert.Contains("HR_max_6", card.Detail);
    }

    [Fact]
    public async Task Card_numbers_are_culture_invariant()
    {
        // .NET renders "P1" as "71.0 %" on Linux (ICU) and "71.0%" on Windows
        // (NLS), and a comma-decimal host would render "0,05". The card must
        // read identically everywhere, so force a culture with both traits.
        var original = CultureInfo.CurrentCulture;
        CultureInfo.CurrentCulture = new CultureInfo("fr-FR");
        try
        {
            var builder = new SepsisRiskCardBuilder(
                new FakeHapiSource(Snapshot),
                new FakeModelScorer(features: Features, result: AboveThreshold));

            CdsCard card = await builder.BuildAsync("p1", prefetchBundleJson: null);

            Assert.StartsWith("Sepsis risk 71.0%", card.Summary);
            Assert.Contains("operating threshold (14.0%)", card.Detail);
            Assert.Contains("Top contributing factors: Lactate_last_24 (+0.05)", card.Detail);
        }
        finally
        {
            CultureInfo.CurrentCulture = original;
        }
    }

    [Fact]
    public async Task Feature_error_becomes_an_info_card_not_a_500()
    {
        var builder = new SepsisRiskCardBuilder(
            new FakeHapiSource(Snapshot),
            new FakeModelScorer(featuresError: new HttpRequestException("boom")));

        CdsCard card = await builder.BuildAsync("p1", null);

        Assert.Equal("info", card.Indicator);
        Assert.Equal("Sepsis risk unavailable", card.Summary);
        Assert.Contains("boom", card.Detail);
    }

    [Fact]
    public async Task Empty_history_is_an_info_card()
    {
        var empty = new PatientSnapshot("p1", [], null, null, null);
        var builder = new SepsisRiskCardBuilder(new FakeHapiSource(empty), new FakeModelScorer());

        CdsCard card = await builder.BuildAsync("p1", null);

        Assert.Equal("info", card.Indicator);
        Assert.Contains("No observations matched", card.Detail);
    }

    [Fact]
    public async Task Unreachable_hapi_is_an_info_card()
    {
        var source = new FakeHapiSource(new PatientSnapshot("p1", [], null, null, null));
        var throwing = new ThrowingHapiSource();
        var builder = new SepsisRiskCardBuilder(throwing, new FakeModelScorer());

        CdsCard card = await builder.BuildAsync("p1", null);

        Assert.Equal("info", card.Indicator);
        Assert.Contains("FHIR server", card.Detail);
    }

    private sealed class ThrowingHapiSource : IHapiSource
    {
        public Task<PatientSnapshot> BuildSnapshotAsync(string patientId, string? prefetchBundleJson) =>
            Task.FromException<PatientSnapshot>(new HttpRequestException("connection refused"));

        public Task<IReadOnlyList<string>> ListPatientsAsync(int limit) =>
            Task.FromException<IReadOnlyList<string>>(new HttpRequestException("connection refused"));
    }
}