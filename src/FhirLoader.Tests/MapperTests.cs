using System;
using System.IO;
using System.Linq;
using Hl7.Fhir.Model;
using Xunit;

namespace FhirLoader.Tests;

/// <summary>Resolve a repo-relative path regardless of where DotNet ran from.</summary>
public static class RepoPath
{
    public static string Resolve(string relative)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, relative)))
        {
            dir = dir.Parent;
        }

        return dir is null
            ? throw new FileNotFoundException($"could not resolve '{relative}' from {AppContext.BaseDirectory}")
            : Path.Combine(dir.FullName, relative);
    }
}

public class FhirClockTests
{
    [Theory]
    [InlineData(1, 1, "2020-01-01T00:00:00Z")]
    [InlineData(24, 1, "2020-01-01T23:00:00Z")]
    [InlineData(5, 5, "2020-01-01T00:00:00Z")] // stays starting at ICULOS 5 align ICULOS 5 to the epoch
    [InlineData(28, 5, "2020-01-01T23:00:00Z")] // 28-5 = 23 hours past the epoch
    public void IculosToInstant_is_epoch_plus_iculos_offset(int iculos, int firstIculos, string expected)
    {
        var instant = FhirClock.IculosToInstant(iculos, firstIculos);
        Assert.Equal(DateTimeOffset.Parse(expected), instant);
    }

    [Fact]
    public void IculosToInstant_rejects_below_one()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => FhirClock.IculosToInstant(0));
    }

    [Fact]
    public void FormatIso_isUTC_and_second_precision()
    {
        Assert.Equal("2020-01-01T05:30:00Z", FhirClock.FormatIso(FhirClock.EpochUtc.AddHours(5.5)));
    }
}

public class ObservationMapperTests
{
    private static SourceRow Row(ICollection<KeyValuePair<string, decimal?>>? cells = null, int iculos = 3)
    {
        var vitals = new Dictionary<string, decimal?>(StringComparer.Ordinal);
        foreach (var v in ClinicalVariables.All) vitals[v] = null;
        foreach (var kv in cells ?? Array.Empty<KeyValuePair<string, decimal?>>()) vitals[kv.Key] = kv.Value;
        return new SourceRow("p000001", "A", iculos - 1, iculos, 62m, 1, 1m, 0m, false, vitals);
    }

    private static LoincEntry Lr = new("HR", LoincMap.LoincSystem, "8867-4", "Heart rate", "{beats}/min", true, "");

    [Fact]
    public void Map_preserves_the_quantity_and_UCUM_unit()
    {
        var obs = ObservationMapper.Map(Row(cells: new[] { new KeyValuePair<string, decimal?>("HR", 92.5m) }), "HR", Lr, firstIculos: 1);
        var q = Assert.IsType<Quantity>(obs.Value);
        Assert.Equal(92.5m, q.Value);
        Assert.Equal("{beats}/min", q.Unit);
        Assert.Equal("{beats}/min", q.Code);
        Assert.Equal(LoincMap.UcumSystem, q.System);
    }

    [Fact]
    public void Map_uses_the_LOINC_code_and_origin_identifier()
    {
        var obs = ObservationMapper.Map(Row(cells: new[] { new KeyValuePair<string, decimal?>("HR", 92m) }), "HR", Lr, firstIculos: 1);
        Assert.Equal(LoincMap.LoincSystem, obs.Code.Coding[0].System);
        Assert.Equal("8867-4", obs.Code.Coding[0].Code);
        Assert.Equal("Heart rate", obs.Code.Coding[0].Display);
        var id = Assert.Single(obs.Identifier);
        Assert.Equal("urn:project:sepsis-cds:origin:observation", id.System);
        Assert.Equal("p000001_HR_3", id.Value);
    }

    [Fact]
    public void Map_attaches_the_correct_effective_instant()
    {
        var obs = ObservationMapper.Map(Row(iculos: 5), "HR", Lr, firstIculos: 1);
        var eff = Assert.IsType<FhirDateTime>(obs.Effective);
        Assert.Equal("2020-01-01T04:00:00Z", eff.Value);
    }

    [Fact]
    public void Map_missing_value_becomes_dataAbsentReason_not_a_drop()
    {
        var obs = ObservationMapper.Map(Row(), "HR", Lr, firstIculos: 1);
        Assert.Null(obs.Value);
        Assert.NotNull(obs.DataAbsentReason);
        Assert.Equal("unknown", obs.DataAbsentReason.Coding[0].Code);
        Assert.Equal(ObservationStatus.Final, obs.Status);
        Assert.Equal("Patient/p000001", obs.Subject.Reference);
    }

    [Fact]
    public void Map_id_is_stable_and_hour_scoped()
    {
        var one = ObservationMapper.Map(Row(iculos: 3), "HR", Lr, firstIculos: 1);
        var two = ObservationMapper.Map(Row(iculos: 3), "HR", Lr, firstIculos: 1);
        var three = ObservationMapper.Map(Row(iculos: 4), "HR", Lr, firstIculos: 1);
        Assert.Equal(one.Id, two.Id);
        Assert.NotEqual(one.Id, three.Id);
    }
}

public class PatientMapperTests
{
    private static SourceRow Row(int? gender, decimal? age) => new(
        "p000001", "A", 0, 1, age, gender, 1m, 0m, false,
        ClinicalVariables.All.ToDictionary(v => v, _ => (decimal?)null));

    [Fact]
    public void Gender_maps_0_Male_1_Female()
    {
        Assert.Equal(AdministrativeGender.Male, PatientMapper.Map(Row(0, 62m), new DateOnly(2020, 1, 1)).Gender);
        Assert.Equal(AdministrativeGender.Female, PatientMapper.Map(Row(1, 62m), new DateOnly(2020, 1, 1)).Gender);
        Assert.Equal(AdministrativeGender.Unknown, PatientMapper.Map(Row(null, 62m), new DateOnly(2020, 1, 1)).Gender);
    }

    [Fact]
    public void BirthDate_is_derived_from_age_as_of_epoch()
    {
        var patient = PatientMapper.Map(Row(1, 62m), new DateOnly(2020, 1, 1));
        Assert.Equal("1958-01-01", patient.BirthDate);
    }

    [Fact]
    public void Missing_age_leaves_birthDate_unset()
    {
        var patient = PatientMapper.Map(Row(1, null), new DateOnly(2020, 1, 1));
        Assert.Null(patient.BirthDate);
    }

    [Fact]
    public void Patient_carries_the_origin_identifier()
    {
        var patient = PatientMapper.Map(Row(1, 62m), new DateOnly(2020, 1, 1));
        Assert.Equal("p000001", patient.Identifier.Single().Value);
    }
}

public class EncounterMapperTests
{
    private static SourceRow Row(int iculos, decimal? unit1 = 1m, decimal? unit2 = 0m) => new(
        "p000001", "A", iculos - 1, iculos, 62m, 1, unit1, unit2, false,
        ClinicalVariables.All.ToDictionary(v => v, _ => (decimal?)null));

    [Fact]
    public void Period_spans_first_to_last_iculos()
    {
        var stay = new[] { Row(5), Row(6), Row(9) };
        var enc = EncounterMapper.Map(stay, "p000001");
        Assert.Equal("2020-01-01T00:00:00Z", enc.Period.Start);
        Assert.Equal("2020-01-01T04:00:00Z", enc.Period.End); // iculos 9 minus first 5 = 4h
    }

    [Fact]
    public void Class_is_inpatient_and_subject_links_the_patient()
    {
        var enc = EncounterMapper.Map(new[] { Row(1) }, "p000001");
        Assert.Equal("IMP", enc.Class.Code);
        Assert.Equal(Encounter.EncounterStatus.Finished, enc.Status);
        Assert.Equal("Patient/p000001", enc.Subject.Reference);
    }

    [Fact]
    public void Unit_extensions_present_only_when_known()
    {
        var withUnits = EncounterMapper.Map(new[] { Row(1, 3m, 5m) }, "p000001");
        Assert.Equal(3, Assert.IsType<Integer>(withUnits.Extension[0].Value).Value);
        Assert.Equal(5, Assert.IsType<Integer>(withUnits.Extension[1].Value).Value);
        Assert.Equal(EncounterMapper.UnitExtensionUrl + "-1", withUnits.Extension[0].Url);

        var without = EncounterMapper.Map(new[] { Row(1, null, null) }, "p000001");
        Assert.Empty(without.Extension);
    }

    [Fact]
    public void Empty_stay_is_rejected()
    {
        Assert.Throws<ArgumentException>(() => EncounterMapper.Map(Array.Empty<SourceRow>(), "p000001"));
    }
}

public class BundleBuilderTests
{
    private static SourceRow Row() => new(
        "p000001", "A", 0, 1, 62m, 1, 1m, 0m, false,
        ClinicalVariables.All.ToDictionary(v => v, _ => (decimal?)null));

    [Fact]
    public void Entries_are_transaction_conditional_creates()
    {
        var bundle = BundleBuilder.Create(new DomainResource[] { PatientMapper.Map(Row(), new DateOnly(2020, 1, 1)) });
        Assert.Equal(Bundle.BundleType.Transaction, bundle.Type);
        var entry = Assert.Single(bundle.Entry);
        Assert.Equal(Bundle.HTTPVerb.POST, entry.Request.Method);
        Assert.Equal("Patient", entry.Request.Url);
        Assert.Equal("identifier=urn:project:sepsis-cds:origin:patient|p000001", entry.Request.IfNoneExist);
    }

    [Fact]
    public void Resource_without_origin_identifier_is_refused()
    {
        var anonymous = new Patient { Id = "x" }; // no identifier at all
        Assert.Throws<ArgumentException>(() => BundleBuilder.Create(new DomainResource[] { anonymous }));
    }
}

public class LoincMapTests
{
    [Fact]
    public void Map_covers_every_clinical_variable_and_marks_unverified()
    {
        var map = LoincMap.Load(RepoPath.Resolve(Path.Combine("src", "FhirLoader", "LoincMap.json")));
        Assert.Equal(35, map.Entries.Count);
        Assert.Equal(ClinicalVariables.All.OrderBy(v => v), map.Entries.Select(e => e.Variable).OrderBy(v => v));
        Assert.NotEmpty(map.Unverified);
        Assert.All(map.Entries, e => Assert.NotEmpty(e.Loinc));
        Assert.All(map.Entries, e => Assert.NotEmpty(e.Ucum));
    }
}