namespace FhirLoader;

/// <summary>
/// Converts the study's relative stay clock (ICULOS, hours since ICU
/// admission) to absolute FHIR instants. The challenge data contains no real
/// calendar dates, so the demo pins every stay to a fixed reference epoch and
/// ICULOS 1 is the stay start instant. Relative ordering and duration are the
/// only clinically meaningful quantities, and they are preserved exactly.
/// </summary>
public static class FhirClock
{
    /// <summary>Fixed reference epoch for the demo. Not a real admission date.</summary>
    public static readonly DateTimeOffset EpochUtc = new(2020, 1, 1, 0, 0, 0, TimeSpan.Zero);

    public static DateTimeOffset IculosToInstant(int iculos, int firstIculos = 1)
    {
        if (iculos < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(iculos), "ICULOS must be >= 1");
        }

        return EpochUtc.AddHours(iculos - firstIculos);
    }

    public static string FormatIso(DateTimeOffset instant) =>
        instant.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture);
}