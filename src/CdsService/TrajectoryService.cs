namespace CdsService;

/// <summary>Thrown when a patient cannot be scored (no data, service down).</summary>
public sealed class TrajectoryUnavailableException(string message) : Exception(message);

/// <summary>
/// Per-hour risk for the dashboard's risk-trajectory view (§13.2). Each hour is
/// scored from only the observations up to that hour — the same feature code
/// path and the same model as the live card, never any future data.
/// </summary>
public sealed class TrajectoryService(IHapiSource hapi, IModelScorer model)
{
    public async Task<TrajectoryResponse> BuildAsync(string patientId, int? shapHour)
    {
        PatientSnapshot snapshot = await hapi.BuildSnapshotAsync(patientId, prefetchBundleJson: null);
        if (snapshot.Observations.Count == 0)
        {
            throw new TrajectoryUnavailableException("no tracked observations for this patient");
        }

        int last = snapshot.CurrentHour;
        int focusHour = Math.Clamp(shapHour ?? last, 0, last);

        var points = new List<RiskPoint>(last + 1);
        ModelResult? focus = null;
        for (int hour = 0; hour <= last; hour++)
        {
            FeatureRow row = await model.BuildFeaturesAsync(snapshot.Prefix(hour));
            ModelResult result = await model.ScoreAsync(row);
            points.Add(new RiskPoint(hour, result.Risk));
            if (hour == focusHour)
            {
                focus = result;
            }
        }

        if (focus is null)
        {
            throw new TrajectoryUnavailableException("the model service did not return a score");
        }

        return new TrajectoryResponse(
            patientId, focus.Threshold, snapshot.OnsetHour, points, focus.TopShap, focusHour);
    }
}