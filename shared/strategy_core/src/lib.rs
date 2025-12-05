use pyo3::{exceptions::PyValueError, prelude::*, types::PyModule, Bound};

fn validate_period(period: usize) -> PyResult<()> {
    if period == 0 {
        Err(PyValueError::new_err("period must be greater than zero"))
    } else {
        Ok(())
    }
}

#[pyfunction]
fn moving_average(prices: Vec<f64>, period: usize) -> PyResult<Option<f64>> {
    validate_period(period)?;

    if prices.len() < period {
        return Ok(None);
    }

    let slice = &prices[prices.len() - period..];
    let sum: f64 = slice.iter().sum();
    Ok(Some(sum / period as f64))
}

#[pyfunction]
fn rsi(prices: Vec<f64>, period: usize) -> PyResult<Option<f64>> {
    validate_period(period)?;

    if prices.len() < period + 1 {
        return Ok(None);
    }

    let mut avg_gain = 0.0;
    let mut avg_loss = 0.0;

    for i in 1..=period {
        let delta = prices[i] - prices[i - 1];
        if delta >= 0.0 {
            avg_gain += delta;
        } else {
            avg_loss -= delta;
        }
    }

    let period_f64 = period as f64;
    avg_gain /= period_f64;
    avg_loss /= period_f64;

    for i in period + 1..prices.len() {
        let delta = prices[i] - prices[i - 1];
        let gain = delta.max(0.0);
        let loss = (-delta).max(0.0);

        avg_gain = ((period_f64 - 1.0) * avg_gain + gain) / period_f64;
        avg_loss = ((period_f64 - 1.0) * avg_loss + loss) / period_f64;
    }

    let rsi = if avg_loss == 0.0 {
        if avg_gain == 0.0 {
            50.0
        } else {
            100.0
        }
    } else {
        let rs = avg_gain / avg_loss;
        100.0 - (100.0 / (1.0 + rs))
    };

    Ok(Some(rsi))
}

#[pyfunction]
fn atr(high: Vec<f64>, low: Vec<f64>, close: Vec<f64>, period: usize) -> PyResult<Option<f64>> {
    validate_period(period)?;

    let len = high.len();
    if len == 0 || low.len() != len || close.len() != len {
        return Err(PyValueError::new_err(
            "high, low, close must have the same non-zero length",
        ));
    }

    if len < period + 1 {
        return Ok(None);
    }

    let mut trs = Vec::with_capacity(len - 1);
    for i in 1..len {
        let tr1 = high[i] - low[i];
        let tr2 = (high[i] - close[i - 1]).abs();
        let tr3 = (low[i] - close[i - 1]).abs();
        let tr = tr1.max(tr2).max(tr3);
        trs.push(tr.abs());
    }

    if trs.len() < period {
        return Ok(None);
    }

    let period_f64 = period as f64;
    let mut atr = trs[..period].iter().sum::<f64>() / period_f64;

    for tr in trs.iter().skip(period) {
        atr = ((period_f64 - 1.0) * atr + tr) / period_f64;
    }

    Ok(Some(atr))
}

#[pymodule]
fn strategy_core(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(moving_average, m)?)?;
    m.add_function(wrap_pyfunction!(rsi, m)?)?;
    m.add_function(wrap_pyfunction!(atr, m)?)?;
    Ok(())
}
