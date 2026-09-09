using Hl7.Fhir.Model;

namespace FhirLoader;

/// <summary>Maps one patient's demographic row to a FHIR R4 Patient.</summary>
public static class PatientMapper
{
    /// <summary>Birth date is derived from Age (years) as of the demo epoch.</summary>
    public static Patient Map(SourceRow row, DateOnly epoch)
    {
        var patient = new Patient
        {
            Id = row.PatientId,
            Meta = new Meta { Profile = new[] { "http://hl7.org/fhir/StructureDefinition/Patient" } },
            Identifier = new List<Identifier> { FhirFactory.PatientIdentifier(row.PatientId) },
            Gender = FhirFactory.GenderCode(row.Gender),
        };

        if (row.Age is decimal ageYears)
        {
            int age = (int)ageYears;
            if (age >= 0)
            {
                patient.BirthDateElement = new Date(epoch.AddYears(-age).ToString("yyyy-MM-dd"));
            }
        }

        return patient;
    }
}