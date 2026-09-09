using Hl7.Fhir.Model;

namespace FhirLoader;

/// <summary>Shared helpers used by the three mappers.</summary>
public static class FhirFactory
{
    /// <summary>
    /// Challenge Gender: 0 -> Male, 1 -> Female, per the MIMIC/PhysioNet
    /// convention used by published entries. See FhirLoader/README.md caveat.
    /// </summary>
    public static AdministrativeGender GenderCode(int? gender) =>
        gender switch
        {
            0 => AdministrativeGender.Male,
            1 => AdministrativeGender.Female,
            _ => AdministrativeGender.Unknown,
        };

    public static Identifier OriginIdentifier(string systemSegment, string value) => new()
    {
        System = LoincMap.OriginSystem + ":" + systemSegment,
        Value = value,
    };

    /// <summary>Patient identifier as used across all resources: urn:...patient|pNNNNNN.</summary>
    public static Identifier PatientIdentifier(string patientId) => OriginIdentifier("patient", patientId);

    public static Quantity Ucum(decimal value, LoincEntry entry) => new()
    {
        Value = value,
        Unit = entry.Ucum,
        Code = entry.Ucum,
        System = LoincMap.UcumSystem,
    };

    /// <summary>Reference to the patient resource within this bundle.</summary>
    public static string PatientRef(string patientId) => $"Patient/{patientId}";
}