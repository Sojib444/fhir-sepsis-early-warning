using Hl7.Fhir.Model;

namespace FhirLoader;

/// <summary>
/// Maps one variable-hour cell of the study to a FHIR R4 Observation.
/// Missingness is preserved (AGENTS.md §2.5): a null value becomes an
/// Observation with no value and a dataAbsentReason of "unknown", so "the lab
/// was not drawn this hour" is queryable rather than silently dropped.
/// </summary>
public static class ObservationMapper
{
    public static Observation Map(SourceRow row, string variable, LoincEntry entry, int firstIculos)
    {
        DateTimeOffset instant = FhirClock.IculosToInstant(row.Iculos, firstIculos);

        var observation = new Observation
        {
            Id = $"{row.PatientId}-{entry.Loinc}-{row.Iculos}",
            Meta = new Meta { Profile = new[] { "http://hl7.org/fhir/StructureDefinition/Observation" } },
            Status = ObservationStatus.Final,
            Code = new CodeableConcept
            {
                Coding = new List<Coding>
                {
                    new Coding(LoincMap.LoincSystem, entry.Loinc, entry.Display),
                },
            },
            Subject = new ResourceReference(FhirFactory.PatientRef(row.PatientId)),
            Effective = new FhirDateTime(FhirClock.FormatIso(instant)),
            Identifier = new List<Identifier>
            {
                FhirFactory.OriginIdentifier("observation", $"{row.PatientId}_{variable}_{row.Iculos}"),
            },
        };

        decimal? value = row.Value(variable);
        if (value is decimal v)
        {
            observation.Value = FhirFactory.Ucum(v, entry);
        }
        else
        {
            observation.DataAbsentReason = new CodeableConcept(
                "http://terminology.hl7.org/CodeSystem/data-absent-reason", "unknown", "unknown");
        }

        return observation;
    }
}