using System.Globalization;

namespace FhirLoader;

/// <summary>
/// One hourly file row. The loader works on a flat CSV exported from
/// data/interim/cohort.parquet (scripts/export_fhirloader.py); nulls are empty
/// cells. All fields are carried as strings and interpreted by the mappers so
/// a parse error names the column instead of dying silently.
/// </summary>
public sealed record SourceRow(
    string PatientId,
    string Site,
    int Hour,
    int Iculos,
    decimal? Age,
    int? Gender,
    decimal? Unit1,
    decimal? Unit2,
    bool SepsisLabel,
    IReadOnlyDictionary<string, decimal?> Vitals)
{
    public decimal? Value(string variable) => Vitals[variable];
}