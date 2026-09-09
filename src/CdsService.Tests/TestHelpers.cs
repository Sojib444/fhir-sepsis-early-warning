using System.Net;
using System.Net.Http;
using Hl7.Fhir.Model;
using Hl7.Fhir.Serialization;
using FhirLoader;

namespace CdsService.Tests;

/// <summary>Shared helpers: repo-relative paths and test doubles.</summary>
public static class TestHelpers
{
    /// <summary>Repository root by walking up from the test output directory.</summary>
    public static string RepoRoot
    {
        get
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "sepsis.slnx")))
            {
                dir = dir.Parent;
            }
            return dir?.FullName ?? throw new InvalidOperationException("repository root not found");
        }
    }

    /// <summary>The shared LOINC table, resolved relative to the repository root.</summary>
    public static string LoincMapPath => Path.Combine(RepoRoot, "src", "FhirLoader", "LoincMap.json");

    public static FhirLoader.LoincMap LoaderLoincMap => FhirLoader.LoincMap.Load(LoincMapPath);

    /// <summary>
    /// A searchset Bundle of loader-identical Observations: one per (iculos,
    /// variable) cell, timestamps pinned to the loader epoch, null value as a
    /// dataAbsentReason Observation. Serialized as a prefetch JSON string.
    /// </summary>
    public static string PrefetchJson(
        string patientId,
        int firstIculos,
        params (int Iculos, string Variable, decimal? Value)[] cells)
    {
        var bundle = new Bundle { Type = Bundle.BundleType.Searchset, Total = cells.Length };
        foreach (var (iculos, variable, value) in cells)
        {
            var row = new SourceRow(
                patientId, "A", iculos - 1, iculos,
                Age: 61m, Gender: 0, Unit1: 1m, Unit2: 0m, SepsisLabel: false,
                Vitals: new Dictionary<string, decimal?> { [variable] = value });
            bundle.Entry.Add(new Bundle.EntryComponent
            {
                Resource = ObservationMapper.Map(row, variable, LoaderLoincMap.For(variable), firstIculos),
            });
        }
        return bundle.ToJson();
    }

    /// <summary>A rigor.json with an alert_burden table, written to a temp file.</summary>
    public static string WriteTempSweep()
    {
        string path = Path.Combine(Path.GetTempPath(), $"rigor-{Guid.NewGuid():N}.json");
        File.WriteAllText(path, """
            {"alert_burden":[
              {"threshold":0.02,"sensitivity":0.91,"ppv":0.02,"alerts_per_100_icu_days":8.1},
              {"threshold":0.50,"sensitivity":0.41,"ppv":0.11,"alerts_per_100_icu_days":1.7}
            ]}
            """);
        return path;
    }
}

/// <summary>Routes relative requests to canned JSON or a status code.</summary>
public sealed class StubRoutes(params (string Path, string Json)[] routes) : HttpMessageHandler
{
    private readonly Dictionary<string, string> _json =
        routes.ToDictionary(r => r.Path, r => r.Json, StringComparer.Ordinal);

    public static HttpClient NewClient(params (string Path, string Json)[] routes) =>
        new(new StubRoutes(routes)) { BaseAddress = new Uri("http://hapi.test/fhir") };

    protected override System.Threading.Tasks.Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        string path = request.RequestUri!.PathAndQuery;
        HttpResponseMessage response = _json.TryGetValue(path, out string? body)
            ? new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(body, System.Text.Encoding.UTF8, "application/json"),
            }
            : new HttpResponseMessage(HttpStatusCode.NotFound);
        return System.Threading.Tasks.Task.FromResult(response);
    }
}

public sealed class FakeHapiSource(PatientSnapshot snapshot, string[]? patientIds = null) : IHapiSource
{
    public System.Threading.Tasks.Task<PatientSnapshot> BuildSnapshotAsync(
        string patientId, string? prefetchBundleJson) =>
        System.Threading.Tasks.Task.FromResult(snapshot);

    public System.Threading.Tasks.Task<IReadOnlyList<string>> ListPatientsAsync(int limit) =>
        System.Threading.Tasks.Task.FromResult<IReadOnlyList<string>>(
            (patientIds ?? [snapshot.PatientId]).Take(limit).ToList());
}

public sealed class FakeModelScorer(
    FeatureRow? features = null,
    ModelResult? result = null,
    Exception? featuresError = null,
    Exception? scoreError = null) : IModelScorer
{
    public System.Threading.Tasks.Task<FeatureRow> BuildFeaturesAsync(PatientSnapshot snapshot) =>
        featuresError is null
            ? System.Threading.Tasks.Task.FromResult(features!)
            : System.Threading.Tasks.Task.FromException<FeatureRow>(featuresError);

    public System.Threading.Tasks.Task<ModelResult> ScoreAsync(FeatureRow row) =>
        scoreError is null
            ? System.Threading.Tasks.Task.FromResult(result!)
            : System.Threading.Tasks.Task.FromException<ModelResult>(scoreError);
}