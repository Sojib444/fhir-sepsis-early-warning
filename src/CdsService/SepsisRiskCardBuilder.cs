namespace CdsService;

/// <summary>
/// Builds the sepsis-risk card for a patient-view hook. Structure only — the
/// numbers and the operating threshold come from the Python model service, so
/// this code can never recompute features differently than training did.
/// </summary>
public sealed class SepsisRiskCardBuilder(IHapiSource hapi, IModelScorer model)
{
    /// <summary>Every patient-facing surface states this (AGENTS.md §1).</summary>
    public const string Disclaimer = "Research prototype — not validated for clinical use.";

    public async Task<CdsCard> BuildAsync(string patientId, string? prefetchBundleJson)
    {
        PatientSnapshot snapshot;
        try
        {
            snapshot = await hapi.BuildSnapshotAsync(patientId, prefetchBundleJson);
        }
        catch (HttpRequestException ex)
        {
            return InfoOnly($"Could not read patient history from the FHIR server: {ex.Message}");
        }

        if (snapshot.Observations.Count == 0)
        {
            return InfoOnly("No observations matched the 34 tracked variables for this patient.");
        }

        FeatureRow features;
        try
        {
            features = await model.BuildFeaturesAsync(snapshot);
        }
        catch (HttpRequestException ex)
        {
            return InfoOnly($"Could not build the feature row: {ex.Message}");
        }

        ModelResult result;
        try
        {
            result = await model.ScoreAsync(features);
        }
        catch (HttpRequestException ex)
        {
            return InfoOnly($"The model service is unreachable: {ex.Message}");
        }

        string topFactors = string.Join(
            ", ",
            result.TopShap.Take(3).Select(t => $"{t.Feature} ({t.Value:+0.00;-0.00;0.00})"));

        string summary = $"Sepsis risk {result.Risk:P1} (operating threshold {result.Threshold:P1})";
        string relation = result.Risk >= result.Threshold ? "above" : "below";
        string detail = $"{Disclaimer} Risk is {relation} the operating threshold ({result.Threshold:P1}). " +
                        $"Top contributing factors: {topFactors}.";
        return new CdsCard(
            Uuid: $"sepsis-risk-{Guid.NewGuid():N}",
            Summary: summary,
            Indicator: result.Indicator,
            Detail: detail,
            SourceLabel: "Sepsis early-warning (research prototype)",
            SourceUrl: "https://github.com/anomalyco/fhir-sepsis-early-warning");
    }

    private static CdsCard InfoOnly(string detail) => new(
        Uuid: $"sepsis-risk-{Guid.NewGuid():N}",
        Summary: "Sepsis risk unavailable",
        Indicator: "info",
        Detail: $"{Disclaimer} {detail}",
        SourceLabel: "Sepsis early-warning (research prototype)",
        SourceUrl: "https://github.com/anomalyco/fhir-sepsis-early-warning");
}