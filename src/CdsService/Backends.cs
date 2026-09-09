using System.Net.Http;

namespace CdsService;

/// <summary>Where patient history comes from (HAPI, or a test double).</summary>
public interface IHapiSource
{
    Task<PatientSnapshot> BuildSnapshotAsync(string patientId, string? prefetchBundleJson);
}

/// <summary>Where scores come from (the Python model service, or a test double).</summary>
public interface IModelScorer
{
    Task<FeatureRow> BuildFeaturesAsync(PatientSnapshot snapshot);
    Task<ModelResult> ScoreAsync(FeatureRow row);
}

public sealed class HapiSource(HttpClient http, LoincMap loinc) : IHapiSource
{
    private readonly HapiClient _client = new(http, loinc);

    public Task<PatientSnapshot> BuildSnapshotAsync(string patientId, string? prefetchBundleJson) =>
        _client.BuildSnapshotAsync(patientId, prefetchBundleJson);
}

public sealed class ModelScorer(HttpClient http) : IModelScorer
{
    private readonly ModelClient _client = new(http);

    public Task<FeatureRow> BuildFeaturesAsync(PatientSnapshot snapshot) =>
        _client.BuildFeaturesAsync(snapshot);

    public Task<ModelResult> ScoreAsync(FeatureRow row) => _client.ScoreAsync(row);
}