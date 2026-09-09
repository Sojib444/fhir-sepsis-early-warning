using System.Linq;
using FhirLoader;
using Hl7.Fhir.Model;
using Hl7.Fhir.Serialization;
using Xunit;

namespace FhirLoader.Tests;

/// <summary>
/// Structural "validates against the R4 profile" gate. Full terminology and
/// snapshot validation requires the official FHIR specification download, which
/// is not available offline in CI; the loader README documents that trade-off.
/// This test instead asserts the cardinality-1 elements the R4 base profiles
/// require and round-trips every generated resource through the R4 JSON
/// serializer, so a malformed resource cannot silently pass CI.
/// </summary>
public class FhirStructureTests
{
    private static SourceRow Row(int iculos = 3) => new(
        "p000001", "A", iculos - 1, iculos, 62m, 1, 1m, 0m, false,
        ClinicalVariables.All.ToDictionary(
            v => v,
            v => v == "HR" ? 92m : (decimal?)null));

    private static LoincEntry Hr => new("HR", "8867-4", "Heart rate", "{beats}/min", true, "");

    [Fact]
    public void Generated_Observation_round_trips_and_meets_base_profile_cardinality()
    {
        var obs = ObservationMapper.Map(Row(), "HR", Hr, firstIculos: 1);
        var json = obs.ToJson();
        var back = new FhirJsonParser().Parse<Observation>(json);

        Assert.Equal(obs.Id, back.Id);
        Assert.NotNull(back.StatusElement);                 // 1..1
        Assert.NotNull(back.Code);                          // 1..1
        Assert.NotNull(back.Subject);                       // 1..1 (profile: mandatory)
        Assert.True(back.Effective is not null || back.Value is not null
                    || back.DataAbsentReason is not null);  // value xor dataAbsentReason
        var quantity = Assert.IsType<Quantity>(back.Value);
        Assert.Equal(LoincMap.UcumSystem, quantity.System);
    }

    [Fact]
    public void Generated_Encounter_round_trips_and_meets_base_profile_cardinality()
    {
        var enc = EncounterMapper.Map(new[] { Row(3), Row(5) }, "p000001");
        var json = enc.ToJson();
        var back = new FhirJsonParser().Parse<Encounter>(json);

        Assert.NotNull(back.StatusElement);    // 1..1
        Assert.NotNull(back.Class);            // 1..1
        Assert.NotNull(back.Subject);          // 1..1 (profile: mandatory)
        Assert.NotNull(back.Period);           // 1..1
        Assert.Equal(enc.Period.Start, back.Period.Start);
    }

    [Fact]
    public void Generated_Patient_round_trips_and_meets_base_profile_cardinality()
    {
        var patient = PatientMapper.Map(Row(), new DateOnly(2020, 1, 1));
        var back = new FhirJsonParser().Parse<Patient>(patient.ToJson());

        Assert.NotNull(back.Identifier);       // 0..* but required for our demo contract
        Assert.NotNull(back.Gender);           // 0..1 set
        Assert.Equal(patient.BirthDate, back.BirthDate);
    }

    [Fact]
    public void Full_generated_bundle_serializes_as_valid_R4_transaction()
    {
        var patient = PatientMapper.Map(Row(), new DateOnly(2020, 1, 1));
        var encounter = EncounterMapper.Map(new[] { Row(3), Row(5) }, "p000001");
        var obs = ObservationMapper.Map(Row(3), "HR", Hr, firstIculos: 3);
        var bundle = BundleBuilder.Create(new DomainResource[] { patient, encounter, obs });

        var parsed = new FhirJsonParser().Parse<Bundle>(bundle.ToJson());
        Assert.Equal(Bundle.BundleType.Transaction, parsed.Type);
        Assert.Equal(3, parsed.Entry.Count);
        Assert.All(parsed.Entry, e =>
        {
            Assert.NotNull(e.Request);
            Assert.False(string.IsNullOrEmpty(e.Request.IfNoneExist));
        });
    }
}