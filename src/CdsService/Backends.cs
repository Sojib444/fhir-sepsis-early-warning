using System.Net.Http;

namespace CdsService;

/// <summary>Where patient history comes from (HAPI, or a test double).</summary>
public interface IHapiSource
{
    Task<PatientSnapshot> BuildSnapshotAsync(string patientId, string? prefetchBundleJson);

    /// <summary>Patient ids in the FHIR server, oldest first (dashboard patient list).</summary>
    Task<IReadOnlyList<string>> ListPatientsAsync(int limit);
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

    public async Task<IReadOnlyList<string>> ListPatientsAsync(int limit)
    {
        string json = await http.GetStringAsync($"/Patient?_count={Math.Clamp(limit, 1, 1000)}&_sort=_id");
        var bundle = new Hl7.Fhir.Serialization.FhirJsonParser()
            .Parse<Hl7.Fhir.Model.Bundle>(json);
        return (bundle.Entry ?? [])
            .Select(e => e.Resource as Hl7.Fhir.Model.Patient)
            .Where(p => p is not null)
            .Select(p => p!.Id)
            .ToList();
    }
}

public sealed class ModelScorer(HttpClient http) : IModelScorer
{
    private readonly ModelClient _client = new(http);

    public Task<FeatureRow> BuildFeaturesAsync(PatientSnapshot snapshot) =>
        _client.BuildFeaturesAsync(snapshot);

    public Task<ModelResult> ScoreAsync(FeatureRow row) => _client.ScoreAsync(row);
}