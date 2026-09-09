using Hl7.Fhir.Model;

namespace FhirLoader;

/// <summary>Maps the ICU stay to a FHIR R4 Encounter (class IMP, inpatient).</summary>
public static class EncounterMapper
{
    public const string UnitExtensionUrl = "urn:project:sepsis-cds:usc-unit";

    public static Encounter Map(
        IEnumerable<SourceRow> stay,
        string patientId)
    {
        var rows = stay.ToList();
        if (rows.Count == 0)
        {
            throw new ArgumentException("an Encounter needs at least one hour row", nameof(stay));
        }

        int firstIculos = rows.Min(r => r.Iculos);
        int lastIculos = rows.Max(r => r.Iculos);
        DateTimeOffset start = FhirClock.IculosToInstant(firstIculos, firstIculos);
        DateTimeOffset end = FhirClock.IculosToInstant(lastIculos, firstIculos);

        var encounter = new Encounter
        {
            Id = patientId,
            Meta = new Meta { Profile = new[] { "http://hl7.org/fhir/StructureDefinition/Encounter" } },
            Status = Encounter.EncounterStatus.Finished,
            Class = new Coding { System = "http://terminology.hl7.org/CodeSystem/v3-ActCode", Code = "IMP", Display = "inpatient encounter" },
            Subject = new ResourceReference(FhirFactory.PatientRef(patientId)),
            Period = new Period
            {
                StartElement = new FhirDateTime(FhirClock.FormatIso(start)),
                EndElement = new FhirDateTime(FhirClock.FormatIso(end)),
            },
            Identifier = new List<Identifier>
            {
                FhirFactory.OriginIdentifier("encounter", patientId),
            },
        };

        // Unit1/Unit2 carry the admission-unit codes from the study; they live
        // in extensions rather than a guessed FHIR value domain.
        var unit1 = rows.Select(r => r.Unit1).FirstOrDefault(u => u.HasValue);
        var unit2 = rows.Select(r => r.Unit2).FirstOrDefault(u => u.HasValue);
        if (unit1 is decimal u1)
        {
            encounter.Extension.Add(new Extension(UnitExtensionUrl + "-1", new Integer((int)u1)));
        }

        if (unit2 is decimal u2)
        {
            encounter.Extension.Add(new Extension(UnitExtensionUrl + "-2", new Integer((int)u2)));
        }

        return encounter;
    }
}