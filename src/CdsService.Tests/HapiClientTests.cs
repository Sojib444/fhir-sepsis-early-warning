using CdsService;
using Hl7.Fhir.Serialization;

namespace CdsService.Tests;

public class HapiClientTests
{
    private readonly HapiClient _client = new(
        StubRoutes.NewClient(), // 404 for /Patient/... — covered by the null/unknown branch
        new LoincMap(TestHelpers.LoincMapPath));

    [Fact]
    public async Task Snapshot_reindexes_hours_and_preserves_values()
    {
        // The loader pins iculos=1 to the epoch, so k = floor(hours since epoch) + 1.
        // min k = 1 => hour starts at 0 regardless of when the stay began.
        string json = TestHelpers.PrefetchJson(
            "p123",
            firstIculos: 1,
            (1, "HR", 80m), (1, "Lactate", 1.2m),
            (2, "HR", 90m),
            (3, "HR", 100m), (3, "Lactate", null));

        // Birth 1958-01-01 => exactly 61 years at the loader epoch.
        var patient = new Hl7.Fhir.Model.Patient
        {
            BirthDate = "1958-01-01",
            Gender = Hl7.Fhir.Model.AdministrativeGender.Male,
        };
        HapiClient client = new(
            StubRoutes.NewClient(("/Patient/p123", patient.ToJson())),
            new LoincMap(TestHelpers.LoincMapPath));

        PatientSnapshot snapshot = await client.BuildSnapshotAsync("p123", json);

        Assert.Equal("p123", snapshot.PatientId);
        Assert.Equal(2, snapshot.CurrentHour);
        Assert.Equal(3, snapshot.Iculos);
        Assert.Equal(61.0, snapshot.Age!.Value, 1.0);
        Assert.Equal(0, snapshot.Gender);

        var byKey = snapshot.Observations.ToDictionary(r => (r.Hour, r.Var), r => r.Value);
        Assert.Equal(80.0, byKey[(0, "HR")]);
        Assert.Equal(1.2, byKey[(0, "Lactate")]);
        Assert.Equal(90.0, byKey[(1, "HR")]);
        Assert.Equal(100.0, byKey[(2, "HR")]);
        Assert.Null(byKey[(2, "Lactate")]); // dataAbsentReason -> null cell
    }

    [Fact]
    public async Task Snapshot_ignores_observations_not_in_loinc_map()
    {
        string json = TestHelpers.PrefetchJson("p456", 1, (1, "HR", 70m));
        // A foreign observation with an unmapped LOINC should not break parsing.
        string wrapped = json.Replace("\"8867-4\"", "\"99999-9\"", StringComparison.Ordinal);

        PatientSnapshot snapshot = await _client.BuildSnapshotAsync("p456", wrapped);
        Assert.Empty(snapshot.Observations);
    }

    [Fact]
    public async Task Snapshot_survives_a_missing_patient_resource()
    {
        // The prefetch supplies observations; the patient lookup 404s. The
        // snapshot must still be built (age/gender stay unknown).
        string json = TestHelpers.PrefetchJson("p789", 1, (1, "O2Sat", 98m));
        PatientSnapshot snapshot = await _client.BuildSnapshotAsync("p789", json);
        Assert.Single(snapshot.Observations);
        Assert.Null(snapshot.Age);
        Assert.Null(snapshot.Gender);
    }
}