using System.Text.Json;
using System.Text.Json.Serialization;

namespace CdsService;

/// <summary>One hourly cell of a measured variable, in training coordinates (hour = 0-based row).</summary>
public sealed record ObservationRow(int Hour, string Var, double? Value);

/// <summary>
/// Everything the model service needs to reconstruct the Phase-3 feature row
/// for the patient's current hour. Observations are strictly hours the
/// patient has already passed (<= t); the model never sees the future (§2.3).
/// </summary>
public sealed record PatientSnapshot(
    string PatientId,
    IReadOnlyList<ObservationRow> Observations,
    double? Age,
    int? Gender,
    int? Iculos,
    int? OnsetHour = null)
{
    /// <summary>Current 0-based hour of the patient (last observed hour).</summary>
    public int CurrentHour => Observations.Count == 0 ? 0 : Observations.Max(o => o.Hour);

    /// <summary>
    /// The stay truncated at hour ≤ <paramref name="hour"/>, with the ICULOS
    /// feature aligned to that row. Used by the trajectory endpoint to score
    /// every historical hour without ever seeing later data.
    /// </summary>
    public PatientSnapshot Prefix(int hour)
    {
        IReadOnlyList<ObservationRow> rows = Observations.Where(o => o.Hour <= hour).ToList();
        int prefixIculos = Iculos is null ? hour + 1 : (Iculos.Value - CurrentHour) + hour;
        return this with { Observations = rows, Iculos = prefixIculos };
    }
}

/// <summary>The feature row the model service produced for the current hour.</summary>
public sealed record FeatureRow(IReadOnlyDictionary<string, double?> Features, int Hour);

public sealed record ShapContribution(string Feature, double Value);

public sealed record ModelResult(
    double Risk,
    double Threshold,
    string Indicator,
    IReadOnlyList<ShapContribution> TopShap);

/// <summary>A CDS Hooks Card, in the fields the spec requires.</summary>
public sealed record CdsCard(
    string Uuid,
    string Summary,
    string Indicator,
    string Detail,
    string SourceLabel,
    string SourceUrl);

public sealed class CdsHookContext
{
    [JsonPropertyName("patientId")]
    public string? PatientId { get; init; }

    [JsonPropertyName("encounterId")]
    public string? EncounterId { get; init; }
}

public sealed class CdsHookRequest
{
    [JsonPropertyName("hook")]
    public string? Hook { get; init; }

    [JsonPropertyName("context")]
    public CdsHookContext? Context { get; init; }

    /// <summary>Serialized Bundle values, either a JSON object or a JSON string.</summary>
    [JsonPropertyName("prefetch")]
    public Dictionary<string, JsonElement>? Prefetch { get; init; }
}

// --- dashboard endpoints (Phase 7) -------------------------------------------

public sealed class TrajectoryRequest
{
    [JsonPropertyName("patientId")]
    public string? PatientId { get; init; }

    /// <summary>Hour whose SHAP contributions the dashboard wants (default: last).</summary>
    [JsonPropertyName("hour")]
    public int? Hour { get; init; }
}

public sealed record RiskPoint(int Hour, double Risk);

public sealed record TrajectoryResponse(
    string PatientId,
    double Threshold,
    int? OnsetHour,
    IReadOnlyList<RiskPoint> Hours,
    IReadOnlyList<ShapContribution> Shap,
    int ShapHour);