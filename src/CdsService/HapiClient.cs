using Hl7.Fhir.Model;
using Hl7.Fhir.Serialization;

namespace CdsService;

/// <summary>Reads a patient's observed history from HAPI FHIR.</summary>
public sealed class HapiClient
{
    /// <summary>
    /// The study-annotation variable (loinc_map.json "SepsisLabel"). It is not
    /// a clinical measurement the model sees; it marks the true onset hour for
    /// the dashboard (§7).
    /// </summary>
    public const string SepsisLabelVariable = "SepsisLabel";

    /// <summary>
    /// The loader pins clock time to this instant for the row ICULOS == 1
    /// (src/FhirLoader/FhirClock.cs). The CDS service reconstructs each hourly
    /// cell's ICULOS from its effective instant against this epoch, then
    /// reindexes to 0 for the feature pipeline — this reproduces the row-index
    /// `hour` the models were trained with.
    /// </summary>
    public static readonly DateTimeOffset Epoch = new(2020, 1, 1, 0, 0, 0, TimeSpan.Zero);

    private static readonly FhirJsonParser Parser = new(new ParserSettings { AllowUnrecognizedEnums = true });
    private readonly HttpClient _http;
    private readonly LoincMap _loinc;

    public HapiClient(HttpClient http, LoincMap loinc)
    {
        _http = http;
        _loinc = loinc;
    }

    /// <summary>
    /// Builds a snapshot from the prefetched bundle when provided (a silent,
    /// read-only CDS Hooks optimization), otherwise queries HAPI. Values are
    /// copied as-is — no unit conversion, because the loader stored the raw
    /// Challenge values and the model was trained on those same numbers.
    /// </summary>
    public async Task<PatientSnapshot> BuildSnapshotAsync(string patientId, string? prefetchBundleJson)
    {
        Bundle? observations = null;
        if (!string.IsNullOrWhiteSpace(prefetchBundleJson))
        {
            observations = Parser.Parse<Bundle>(prefetchBundleJson);
        }
        else
        {
            string json = await _http.GetStringAsync(
                $"/Observation?subject=Patient/{patientId}&_sort=date&_count=10000");
            observations = Parser.Parse<Bundle>(json);
        }

        Patient? patient = null;
        try
        {
            string patientJson = await _http.GetStringAsync($"/Patient/{patientId}");
            patient = Parser.Parse<Patient>(patientJson);
        }
        catch (HttpRequestException)
        {
            // Patient 404/403 on a foreign server: features still work, the
            // demographics stay unknown.
        }

        return BuildSnapshot(patientId, observations, patient);
    }

    public PatientSnapshot BuildSnapshot(string patientId, Bundle observations, Patient? patient)
    {
        List<(int Iculos, string Var, double? Value)> cells = new();

        foreach (Bundle.EntryComponent entry in observations.Entry ?? [])
        {
            if (entry.Resource is not Observation obs) continue;
            string? variable = _loinc.VariableForCode(obs.Code?.Coding?.FirstOrDefault()?.Code);
            if (variable is null) continue;

            double? value = (obs.Value as Quantity)?.Value is { } raw ? (double)raw : null;
            DateTimeOffset? instant = ObsInstant(obs);
            if (instant is null) continue;

            int iculos = (int)Math.Floor((instant.Value - Epoch).TotalHours) + 1;
            cells.Add((iculos, variable, value));
        }

        int minIculos = cells.Count == 0 ? 0 : cells.Min(c => c.Iculos);

        // Feature rows exclude the study label (it is not a clinical variable the
        // model saw); the label instead pins the true onset hour for the dashboard.
        List<ObservationRow> rows = cells
            .Where(c => c.Var != SepsisLabelVariable)
            .Select(c => new ObservationRow(c.Iculos - minIculos, c.Var, c.Value))
            .OrderBy(r => r.Hour)
            .ThenBy(r => r.Var)
            .ToList();

        int? onsetHour = cells
            .Where(c => c.Var == SepsisLabelVariable && c.Value == 1.0)
            .Select(c => c.Iculos - minIculos)
            .OrderBy(h => h)
            .Cast<int?>()
            .FirstOrDefault();

        int currentIculos = cells.Count == 0 ? 1 : cells.Max(c => c.Iculos);
        double? age = PatientAgeIn2019(patient);
        int? gender = patient?.Gender switch
        {
            AdministrativeGender.Male => 0,
            AdministrativeGender.Female => 1,
            _ => null,
        };
        return new PatientSnapshot(patientId, rows, age, gender, currentIculos, onsetHour);
    }

    private static DateTimeOffset? ObsInstant(Observation obs)
    {
        // The loader writes effective as an instant string ("2020-01-01T03:00:00+00:00").
        // Firely's DataType.ToString() round-trips that string, so parsing it is
        // version-proof against Instant/FhirDateTime/Period representation drift.
        string? iso = obs.Effective is Period period ? (period.Start ?? period.End) : obs.Effective.ToString();
        return DateTimeOffset.TryParse(
            iso,
            System.Globalization.CultureInfo.InvariantCulture,
            System.Globalization.DateTimeStyles.AssumeUniversal,
            out DateTimeOffset parsed)
            ? parsed
            : null;
    }

    /// <summary>
    /// Age at the loader's epoch (2019, matching the cohort mid-point). The
    /// model sees Age in integer years, so the exact anchor barely matters.
    /// </summary>
    private static double? PatientAgeIn2019(Patient? patient)
    {
        if (DateTimeOffset.TryParse(
                patient?.BirthDate,
                System.Globalization.CultureInfo.InvariantCulture,
                System.Globalization.DateTimeStyles.AssumeUniversal,
                out DateTimeOffset birth))
        {
            return (Epoch - birth).TotalDays / 365.25;
        }
        return null;
    }
}