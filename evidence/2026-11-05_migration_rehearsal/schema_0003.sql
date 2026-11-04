--
-- PostgreSQL database dump
--

\restrict ydV4bz5e9fFiF0veWuud9QramnyfQryYDjerGweW1Qo47wCSFM6YbjV6R98lJpb

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg13+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: aqt_forbid_change(); Type: FUNCTION; Schema: public; Owner: aqt
--

CREATE FUNCTION public.aqt_forbid_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END $$;


ALTER FUNCTION public.aqt_forbid_change() OWNER TO aqt;

--
-- Name: aqt_hash_chain(); Type: FUNCTION; Schema: public; Owner: aqt
--

CREATE FUNCTION public.aqt_hash_chain() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE prev text;
    BEGIN
        PERFORM pg_advisory_xact_lock(hashtext('aqt_chain_' || TG_TABLE_NAME));
        EXECUTE format('SELECT row_hash FROM %I ORDER BY id DESC LIMIT 1', TG_TABLE_NAME) INTO prev;
        NEW.prev_hash := COALESCE(prev, repeat('0', 64));
        NEW.row_hash := NULL;
        NEW.row_hash := encode(sha256(convert_to(NEW.prev_hash || row_to_json(NEW)::text, 'UTF8')), 'hex');
        RETURN NEW;
    END $$;


ALTER FUNCTION public.aqt_hash_chain() OWNER TO aqt;

--
-- Name: aqt_order_guard(); Type: FUNCTION; Schema: public; Owner: aqt
--

CREATE FUNCTION public.aqt_order_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'orders are never deleted' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.reason IS DISTINCT FROM OLD.reason OR NEW.quantity IS DISTINCT FROM OLD.quantity
           OR NEW.limit_price IS DISTINCT FROM OLD.limit_price OR NEW.side IS DISTINCT FROM OLD.side
           OR NEW.security_id IS DISTINCT FROM OLD.security_id OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'order decision fields are immutable' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;


ALTER FUNCTION public.aqt_order_guard() OWNER TO aqt;

--
-- Name: aqt_raw_payload_guard(); Type: FUNCTION; Schema: public; Owner: aqt
--

CREATE FUNCTION public.aqt_raw_payload_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'raw_payloads rows cannot be deleted' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.content_gz IS DISTINCT FROM OLD.content_gz OR NEW.sha256 IS DISTINCT FROM OLD.sha256
           OR NEW.fetched_at IS DISTINCT FROM OLD.fetched_at OR NEW.url IS DISTINCT FROM OLD.url THEN
            RAISE EXCEPTION 'raw payload content is immutable' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;


ALTER FUNCTION public.aqt_raw_payload_guard() OWNER TO aqt;

--
-- Name: aqt_strategy_version_guard(); Type: FUNCTION; Schema: public; Owner: aqt
--

CREATE FUNCTION public.aqt_strategy_version_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'strategy versions are never deleted (retire them instead)'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.params IS DISTINCT FROM OLD.params OR NEW.version IS DISTINCT FROM OLD.version
           OR NEW.strategy_id IS DISTINCT FROM OLD.strategy_id OR NEW.change_reason IS DISTINCT FROM OLD.change_reason
           OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id OR NEW.code_ref IS DISTINCT FROM OLD.code_ref THEN
            RAISE EXCEPTION 'strategy version definition is immutable; create a new version'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END $$;


ALTER FUNCTION public.aqt_strategy_version_guard() OWNER TO aqt;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account_settings; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.account_settings (
    account_id integer NOT NULL,
    risk_level character varying(16) NOT NULL,
    position_size_pct numeric(6,4) NOT NULL,
    max_positions integer NOT NULL,
    max_daily_loss_pct numeric(6,4) NOT NULL,
    max_drawdown_pct numeric(6,4) NOT NULL,
    stop_loss_pct numeric(6,4) NOT NULL,
    take_profit_pct numeric(6,4),
    trailing_stop_pct numeric(6,4),
    allow_day_trade boolean NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.account_settings OWNER TO aqt;

--
-- Name: account_settings_history; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.account_settings_history (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    settings jsonb NOT NULL,
    changed_by character varying(32) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.account_settings_history OWNER TO aqt;

--
-- Name: account_settings_history_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.account_settings_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.account_settings_history_id_seq OWNER TO aqt;

--
-- Name: account_settings_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.account_settings_history_id_seq OWNED BY public.account_settings_history.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO aqt;

--
-- Name: backtest_runs; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.backtest_runs (
    id bigint NOT NULL,
    strategy_version_id integer NOT NULL,
    kind character varying(16) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    universe_rule character varying(64) NOT NULL,
    cost_model jsonb NOT NULL,
    metrics jsonb NOT NULL,
    regime_metrics jsonb NOT NULL,
    equity_curve jsonb NOT NULL,
    data_snapshot_at timestamp with time zone NOT NULL,
    code_ref character varying(80),
    status character varying(12) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.backtest_runs OWNER TO aqt;

--
-- Name: backtest_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.backtest_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.backtest_runs_id_seq OWNER TO aqt;

--
-- Name: backtest_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.backtest_runs_id_seq OWNED BY public.backtest_runs.id;


--
-- Name: backtest_trades; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.backtest_trades (
    id bigint NOT NULL,
    backtest_run_id bigint NOT NULL,
    security_id integer NOT NULL,
    entry_date date NOT NULL,
    exit_date date,
    entry_price double precision NOT NULL,
    exit_price double precision,
    quantity integer NOT NULL,
    costs double precision NOT NULL,
    net_pnl double precision,
    exit_reason character varying(32)
);


ALTER TABLE public.backtest_trades OWNER TO aqt;

--
-- Name: backtest_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.backtest_trades_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.backtest_trades_id_seq OWNER TO aqt;

--
-- Name: backtest_trades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.backtest_trades_id_seq OWNED BY public.backtest_trades.id;


--
-- Name: cash_ledger; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.cash_ledger (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    entry_time timestamp with time zone NOT NULL,
    entry_type character varying(16) NOT NULL,
    amount numeric(18,4) NOT NULL,
    balance_after numeric(18,4) NOT NULL,
    fill_id bigint,
    settlement_date date,
    prev_hash character varying(64),
    row_hash character varying(64)
);


ALTER TABLE public.cash_ledger OWNER TO aqt;

--
-- Name: cash_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.cash_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cash_ledger_id_seq OWNER TO aqt;

--
-- Name: cash_ledger_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.cash_ledger_id_seq OWNED BY public.cash_ledger.id;


--
-- Name: closed_trades; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.closed_trades (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    security_id integer NOT NULL,
    strategy_version_id integer,
    entry_time timestamp with time zone NOT NULL,
    exit_time timestamp with time zone NOT NULL,
    quantity integer NOT NULL,
    entry_cost numeric(18,4) NOT NULL,
    exit_proceeds numeric(18,4) NOT NULL,
    total_costs numeric(18,4) NOT NULL,
    net_pnl numeric(18,4) NOT NULL,
    return_pct numeric(12,6) NOT NULL,
    holding_days integer NOT NULL,
    exit_reason character varying(32) NOT NULL,
    entry_fill_ids jsonb NOT NULL,
    exit_fill_ids jsonb NOT NULL
);


ALTER TABLE public.closed_trades OWNER TO aqt;

--
-- Name: closed_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.closed_trades_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.closed_trades_id_seq OWNER TO aqt;

--
-- Name: closed_trades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.closed_trades_id_seq OWNED BY public.closed_trades.id;


--
-- Name: corporate_actions; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.corporate_actions (
    id integer NOT NULL,
    security_id integer NOT NULL,
    ex_date date NOT NULL,
    action_type character varying(24) NOT NULL,
    prev_close numeric(12,4),
    ref_price numeric(12,4),
    cash_dividend numeric(12,6),
    stock_dividend_ratio numeric(12,8),
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.corporate_actions OWNER TO aqt;

--
-- Name: corporate_actions_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.corporate_actions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.corporate_actions_id_seq OWNER TO aqt;

--
-- Name: corporate_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.corporate_actions_id_seq OWNED BY public.corporate_actions.id;


--
-- Name: daily_prices; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.daily_prices (
    security_id integer NOT NULL,
    trade_date date NOT NULL,
    open numeric(12,4),
    high numeric(12,4),
    low numeric(12,4),
    close numeric(12,4),
    change numeric(12,4),
    volume bigint NOT NULL,
    turnover bigint NOT NULL,
    transactions integer,
    prev_close numeric(12,4),
    limit_up numeric(12,4),
    limit_down numeric(12,4),
    adj_factor numeric(18,10) NOT NULL,
    is_suspended boolean NOT NULL,
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL,
    fetched_at timestamp with time zone NOT NULL
);


ALTER TABLE public.daily_prices OWNER TO aqt;

--
-- Name: data_quality_issues; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.data_quality_issues (
    id bigint NOT NULL,
    check_name character varying(64) NOT NULL,
    severity character varying(8) NOT NULL,
    dataset character varying(64) NOT NULL,
    security_id integer,
    trade_date date,
    detail jsonb NOT NULL,
    resolved boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    classification character varying(32),
    blocks_backtest boolean DEFAULT false NOT NULL,
    reviewed_at timestamp with time zone
);


ALTER TABLE public.data_quality_issues OWNER TO aqt;

--
-- Name: data_quality_issues_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.data_quality_issues_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.data_quality_issues_id_seq OWNER TO aqt;

--
-- Name: data_quality_issues_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.data_quality_issues_id_seq OWNED BY public.data_quality_issues.id;


--
-- Name: data_source_status; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.data_source_status (
    source character varying(32) NOT NULL,
    dataset character varying(64) NOT NULL,
    status character varying(12) NOT NULL,
    last_success_at timestamp with time zone,
    last_failure_at timestamp with time zone,
    last_error text,
    consecutive_failures integer NOT NULL,
    latest_data_date date,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.data_source_status OWNER TO aqt;

--
-- Name: equity_snapshots; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.equity_snapshots (
    account_id integer NOT NULL,
    snap_date date NOT NULL,
    cash numeric(18,4) NOT NULL,
    market_value numeric(18,4) NOT NULL,
    total_equity numeric(18,4) NOT NULL,
    realized_pnl numeric(18,4) NOT NULL,
    unrealized_pnl numeric(18,4) NOT NULL,
    peak_equity numeric(18,4) NOT NULL,
    drawdown_pct numeric(10,6) NOT NULL,
    benchmark_close numeric(14,4)
);


ALTER TABLE public.equity_snapshots OWNER TO aqt;

--
-- Name: fills; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.fills (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    account_id integer NOT NULL,
    security_id integer NOT NULL,
    side character varying(4) NOT NULL,
    fill_time timestamp with time zone NOT NULL,
    reference_price numeric(12,4) NOT NULL,
    fill_price numeric(12,4) NOT NULL,
    quantity integer NOT NULL,
    gross_amount numeric(18,4) NOT NULL,
    commission numeric(18,4) NOT NULL,
    tax numeric(18,4) NOT NULL,
    slippage_cost numeric(18,4) NOT NULL,
    net_amount numeric(18,4) NOT NULL,
    price_source character varying(32) NOT NULL,
    price_delay_seconds double precision,
    prev_hash character varying(64),
    row_hash character varying(64)
);


ALTER TABLE public.fills OWNER TO aqt;

--
-- Name: fills_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.fills_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.fills_id_seq OWNER TO aqt;

--
-- Name: fills_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.fills_id_seq OWNED BY public.fills.id;


--
-- Name: financial_statements; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.financial_statements (
    security_id integer NOT NULL,
    fiscal_year integer NOT NULL,
    fiscal_quarter integer NOT NULL,
    basis character varying(12) NOT NULL,
    revenue bigint,
    gross_profit bigint,
    operating_income bigint,
    net_income bigint,
    eps numeric(12,4),
    gross_margin numeric(14,6),
    operating_margin numeric(14,6),
    total_assets bigint,
    total_liabilities bigint,
    equity bigint,
    published_date date,
    available_at timestamp with time zone NOT NULL,
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    published_at timestamp with time zone,
    published_basis character varying(24) DEFAULT 'UNKNOWN'::character varying NOT NULL,
    pit_reliable boolean DEFAULT false NOT NULL
);


ALTER TABLE public.financial_statements OWNER TO aqt;

--
-- Name: institutional_trades; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.institutional_trades (
    security_id integer NOT NULL,
    trade_date date NOT NULL,
    foreign_buy bigint,
    foreign_sell bigint,
    foreign_net bigint,
    foreign_dealer_net bigint,
    trust_buy bigint,
    trust_sell bigint,
    trust_net bigint,
    dealer_net bigint,
    dealer_self_net bigint,
    dealer_hedge_net bigint,
    total_net bigint,
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.institutional_trades OWNER TO aqt;

--
-- Name: intraday_quotes; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.intraday_quotes (
    id bigint NOT NULL,
    security_id integer NOT NULL,
    quote_time timestamp with time zone NOT NULL,
    price numeric(12,4),
    cum_volume bigint,
    best_bid numeric(12,4),
    best_ask numeric(12,4),
    source character varying(32) NOT NULL,
    delay_seconds double precision,
    fetched_at timestamp with time zone NOT NULL
);


ALTER TABLE public.intraday_quotes OWNER TO aqt;

--
-- Name: intraday_quotes_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.intraday_quotes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.intraday_quotes_id_seq OWNER TO aqt;

--
-- Name: intraday_quotes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.intraday_quotes_id_seq OWNED BY public.intraday_quotes.id;


--
-- Name: job_runs; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.job_runs (
    id bigint NOT NULL,
    job_name character varying(64) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    status character varying(12) NOT NULL,
    params jsonb NOT NULL,
    result jsonb NOT NULL,
    error text
);


ALTER TABLE public.job_runs OWNER TO aqt;

--
-- Name: job_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.job_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.job_runs_id_seq OWNER TO aqt;

--
-- Name: job_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.job_runs_id_seq OWNED BY public.job_runs.id;


--
-- Name: llm_calls; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.llm_calls (
    id bigint NOT NULL,
    purpose character varying(64) NOT NULL,
    model character varying(64) NOT NULL,
    input_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    cost_usd double precision NOT NULL,
    latency_ms integer,
    prompt_sha256 character varying(64),
    status character varying(12) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.llm_calls OWNER TO aqt;

--
-- Name: llm_calls_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.llm_calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.llm_calls_id_seq OWNER TO aqt;

--
-- Name: llm_calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.llm_calls_id_seq OWNED BY public.llm_calls.id;


--
-- Name: margin_trades; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.margin_trades (
    security_id integer NOT NULL,
    trade_date date NOT NULL,
    margin_buy bigint,
    margin_sell bigint,
    margin_redeem bigint,
    margin_balance_prev bigint,
    margin_balance bigint,
    margin_limit bigint,
    short_cover bigint,
    short_sell bigint,
    short_redeem bigint,
    short_balance_prev bigint,
    short_balance bigint,
    short_limit bigint,
    "offset" bigint,
    note character varying(64),
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.margin_trades OWNER TO aqt;

--
-- Name: market_indices; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.market_indices (
    index_code character varying(32) NOT NULL,
    trade_date date NOT NULL,
    name character varying(64) NOT NULL,
    close numeric(14,4),
    change numeric(14,4),
    change_pct numeric(10,4),
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.market_indices OWNER TO aqt;

--
-- Name: market_regimes; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.market_regimes (
    regime_date date NOT NULL,
    regime character varying(24) NOT NULL,
    trend_score double precision,
    volatility double precision,
    breadth double precision,
    detail jsonb NOT NULL
);


ALTER TABLE public.market_regimes OWNER TO aqt;

--
-- Name: monthly_revenues; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.monthly_revenues (
    security_id integer NOT NULL,
    period date NOT NULL,
    revenue bigint,
    revenue_prev_month bigint,
    revenue_last_year bigint,
    mom_pct numeric(14,6),
    yoy_pct numeric(14,6),
    cum_revenue bigint,
    cum_revenue_last_year bigint,
    cum_yoy_pct numeric(14,6),
    published_date date,
    available_at timestamp with time zone NOT NULL,
    source character varying(32) NOT NULL,
    raw_payload_id bigint
);


ALTER TABLE public.monthly_revenues OWNER TO aqt;

--
-- Name: notifications; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.notifications (
    id bigint NOT NULL,
    level character varying(8) NOT NULL,
    title character varying(200) NOT NULL,
    body text NOT NULL,
    channel character varying(16) NOT NULL,
    sent_at timestamp with time zone,
    read_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.notifications OWNER TO aqt;

--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.notifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.notifications_id_seq OWNER TO aqt;

--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: order_events; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.order_events (
    id bigint NOT NULL,
    order_id bigint NOT NULL,
    event_time timestamp with time zone NOT NULL,
    event_type character varying(16) NOT NULL,
    detail jsonb NOT NULL,
    prev_hash character varying(64),
    row_hash character varying(64)
);


ALTER TABLE public.order_events OWNER TO aqt;

--
-- Name: order_events_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.order_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.order_events_id_seq OWNER TO aqt;

--
-- Name: order_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.order_events_id_seq OWNED BY public.order_events.id;


--
-- Name: orders; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.orders (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    decision_id bigint,
    security_id integer NOT NULL,
    side character varying(4) NOT NULL,
    order_type character varying(8) NOT NULL,
    time_in_force character varying(8) NOT NULL,
    limit_price numeric(12,4),
    quantity integer NOT NULL,
    filled_quantity integer NOT NULL,
    status character varying(16) NOT NULL,
    reject_reason text,
    strategy_version_id integer,
    reason text NOT NULL,
    stop_price numeric(12,4),
    target_price numeric(12,4),
    market_regime character varying(32),
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_orders_positive_qty CHECK ((quantity > 0))
);


ALTER TABLE public.orders OWNER TO aqt;

--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.orders_id_seq OWNER TO aqt;

--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: positions; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.positions (
    account_id integer NOT NULL,
    security_id integer NOT NULL,
    quantity integer NOT NULL,
    avg_cost numeric(12,4) NOT NULL,
    opened_at timestamp with time zone NOT NULL,
    stop_price numeric(12,4),
    target_price numeric(12,4),
    strategy_version_id integer,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.positions OWNER TO aqt;

--
-- Name: raw_payloads; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.raw_payloads (
    id bigint NOT NULL,
    source character varying(32) NOT NULL,
    dataset character varying(64) NOT NULL,
    url text NOT NULL,
    params jsonb NOT NULL,
    data_date date,
    fetched_at timestamp with time zone NOT NULL,
    http_status integer,
    sha256 character varying(64) NOT NULL,
    content_gz bytea NOT NULL,
    parse_status character varying(16) NOT NULL,
    parse_error text
);


ALTER TABLE public.raw_payloads OWNER TO aqt;

--
-- Name: raw_payloads_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.raw_payloads_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.raw_payloads_id_seq OWNER TO aqt;

--
-- Name: raw_payloads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.raw_payloads_id_seq OWNED BY public.raw_payloads.id;


--
-- Name: research_proposals; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.research_proposals (
    id bigint NOT NULL,
    origin_review_id bigint,
    weakness text NOT NULL,
    hypothesis text NOT NULL,
    plan text NOT NULL,
    status character varying(16) NOT NULL,
    candidate_version_id integer,
    conclusion text,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.research_proposals OWNER TO aqt;

--
-- Name: research_proposals_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.research_proposals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.research_proposals_id_seq OWNER TO aqt;

--
-- Name: research_proposals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.research_proposals_id_seq OWNED BY public.research_proposals.id;


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.reviews (
    id bigint NOT NULL,
    period_type character varying(8) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    title character varying(200) NOT NULL,
    content_md text NOT NULL,
    findings jsonb NOT NULL,
    llm_call_id bigint,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.reviews OWNER TO aqt;

--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reviews_id_seq OWNER TO aqt;

--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: scan_runs; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.scan_runs (
    id bigint NOT NULL,
    scan_date date NOT NULL,
    data_cutoff_at timestamp with time zone NOT NULL,
    universe_count integer NOT NULL,
    excluded_count integer NOT NULL,
    exclusions jsonb NOT NULL,
    scoring_version character varying(16) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.scan_runs OWNER TO aqt;

--
-- Name: scan_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.scan_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.scan_runs_id_seq OWNER TO aqt;

--
-- Name: scan_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.scan_runs_id_seq OWNED BY public.scan_runs.id;


--
-- Name: securities; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.securities (
    id integer NOT NULL,
    code character varying(12) NOT NULL,
    market character varying(8) NOT NULL,
    name character varying(64) NOT NULL,
    security_type character varying(16) NOT NULL,
    industry character varying(64),
    isin character varying(16),
    listed_date date,
    delisted_date date,
    status character varying(16) NOT NULL,
    first_seen_date date,
    last_seen_date date,
    updated_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.securities OWNER TO aqt;

--
-- Name: securities_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.securities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.securities_id_seq OWNER TO aqt;

--
-- Name: securities_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.securities_id_seq OWNED BY public.securities.id;


--
-- Name: sim_accounts; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.sim_accounts (
    id integer NOT NULL,
    name character varying(64) NOT NULL,
    mode character varying(16) NOT NULL,
    initial_capital numeric(18,4) NOT NULL,
    status character varying(12) NOT NULL,
    started_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_sim_accounts_positive_capital CHECK ((initial_capital > (0)::numeric)),
    CONSTRAINT ck_sim_accounts_simulation_only CHECK (((mode)::text = 'SIMULATION'::text))
);


ALTER TABLE public.sim_accounts OWNER TO aqt;

--
-- Name: sim_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.sim_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sim_accounts_id_seq OWNER TO aqt;

--
-- Name: sim_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.sim_accounts_id_seq OWNED BY public.sim_accounts.id;


--
-- Name: stock_scores; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.stock_scores (
    scan_run_id bigint NOT NULL,
    security_id integer NOT NULL,
    rank integer NOT NULL,
    total double precision NOT NULL,
    trend double precision,
    momentum double precision,
    volume double precision,
    chip double precision,
    fundamental double precision,
    risk double precision,
    regime_fit double precision,
    detail jsonb NOT NULL
);


ALTER TABLE public.stock_scores OWNER TO aqt;

--
-- Name: strategies; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.strategies (
    id integer NOT NULL,
    code character varying(48) NOT NULL,
    name character varying(100) NOT NULL,
    horizon character varying(8) NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.strategies OWNER TO aqt;

--
-- Name: strategies_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.strategies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.strategies_id_seq OWNER TO aqt;

--
-- Name: strategies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.strategies_id_seq OWNED BY public.strategies.id;


--
-- Name: strategy_stage_changes; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.strategy_stage_changes (
    id bigint NOT NULL,
    strategy_version_id integer NOT NULL,
    from_stage character varying(16),
    to_stage character varying(16) NOT NULL,
    reason text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.strategy_stage_changes OWNER TO aqt;

--
-- Name: strategy_stage_changes_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.strategy_stage_changes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.strategy_stage_changes_id_seq OWNER TO aqt;

--
-- Name: strategy_stage_changes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.strategy_stage_changes_id_seq OWNED BY public.strategy_stage_changes.id;


--
-- Name: strategy_versions; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.strategy_versions (
    id integer NOT NULL,
    strategy_id integer NOT NULL,
    version character varying(16) NOT NULL,
    parent_version_id integer,
    params jsonb NOT NULL,
    code_ref character varying(80),
    change_reason text NOT NULL,
    stage character varying(16) NOT NULL,
    promoted_at timestamp with time zone,
    retired_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.strategy_versions OWNER TO aqt;

--
-- Name: strategy_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.strategy_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.strategy_versions_id_seq OWNER TO aqt;

--
-- Name: strategy_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.strategy_versions_id_seq OWNED BY public.strategy_versions.id;


--
-- Name: system_logs; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.system_logs (
    id bigint NOT NULL,
    level character varying(8) NOT NULL,
    component character varying(64) NOT NULL,
    message text NOT NULL,
    detail jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL
);


ALTER TABLE public.system_logs OWNER TO aqt;

--
-- Name: system_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.system_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.system_logs_id_seq OWNER TO aqt;

--
-- Name: system_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.system_logs_id_seq OWNED BY public.system_logs.id;


--
-- Name: system_state; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.system_state (
    key character varying(64) NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


ALTER TABLE public.system_state OWNER TO aqt;

--
-- Name: technical_indicators; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.technical_indicators (
    security_id integer NOT NULL,
    trade_date date NOT NULL,
    ma5 double precision,
    ma10 double precision,
    ma20 double precision,
    ma60 double precision,
    k9 double precision,
    d9 double precision,
    rsi6 double precision,
    rsi14 double precision,
    macd_dif double precision,
    macd_signal double precision,
    macd_hist double precision,
    bb_upper double precision,
    bb_mid double precision,
    bb_lower double precision,
    vol_ma5 double precision,
    vol_ma20 double precision,
    turnover_ma20 double precision,
    atr14 double precision,
    high_20 double precision,
    low_20 double precision,
    high_60 double precision,
    volume_ratio double precision,
    calc_version character varying(16) NOT NULL,
    computed_at timestamp with time zone NOT NULL
);


ALTER TABLE public.technical_indicators OWNER TO aqt;

--
-- Name: trade_decisions; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.trade_decisions (
    id bigint NOT NULL,
    account_id integer NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    data_cutoff_at timestamp with time zone NOT NULL,
    security_id integer,
    action character varying(8) NOT NULL,
    strategy_version_id integer,
    market_regime character varying(32),
    score double precision,
    reasoning text NOT NULL,
    analyst_inputs jsonb NOT NULL,
    risk_check jsonb NOT NULL,
    approved boolean NOT NULL,
    planned_stop numeric(12,4),
    planned_target numeric(12,4),
    llm_call_id bigint,
    prev_hash character varying(64),
    row_hash character varying(64)
);


ALTER TABLE public.trade_decisions OWNER TO aqt;

--
-- Name: trade_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.trade_decisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trade_decisions_id_seq OWNER TO aqt;

--
-- Name: trade_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.trade_decisions_id_seq OWNED BY public.trade_decisions.id;


--
-- Name: trading_calendar; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.trading_calendar (
    market character varying(8) NOT NULL,
    cal_date date NOT NULL,
    is_trading_day boolean NOT NULL,
    source character varying(32) NOT NULL,
    note character varying(128)
);


ALTER TABLE public.trading_calendar OWNER TO aqt;

--
-- Name: trading_restrictions; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.trading_restrictions (
    id bigint NOT NULL,
    security_id integer NOT NULL,
    restriction_type character varying(24) NOT NULL,
    start_date date NOT NULL,
    end_date date,
    announced_date date,
    detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.trading_restrictions OWNER TO aqt;

--
-- Name: trading_restrictions_id_seq; Type: SEQUENCE; Schema: public; Owner: aqt
--

CREATE SEQUENCE public.trading_restrictions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.trading_restrictions_id_seq OWNER TO aqt;

--
-- Name: trading_restrictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: aqt
--

ALTER SEQUENCE public.trading_restrictions_id_seq OWNED BY public.trading_restrictions.id;


--
-- Name: valuations; Type: TABLE; Schema: public; Owner: aqt
--

CREATE TABLE public.valuations (
    security_id integer NOT NULL,
    trade_date date NOT NULL,
    pe_ratio numeric(14,6),
    pb_ratio numeric(14,6),
    dividend_yield numeric(14,6),
    source character varying(32) NOT NULL,
    raw_payload_id bigint,
    available_at timestamp with time zone NOT NULL
);


ALTER TABLE public.valuations OWNER TO aqt;

--
-- Name: account_settings_history id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.account_settings_history ALTER COLUMN id SET DEFAULT nextval('public.account_settings_history_id_seq'::regclass);


--
-- Name: backtest_runs id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_runs ALTER COLUMN id SET DEFAULT nextval('public.backtest_runs_id_seq'::regclass);


--
-- Name: backtest_trades id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_trades ALTER COLUMN id SET DEFAULT nextval('public.backtest_trades_id_seq'::regclass);


--
-- Name: cash_ledger id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.cash_ledger ALTER COLUMN id SET DEFAULT nextval('public.cash_ledger_id_seq'::regclass);


--
-- Name: closed_trades id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.closed_trades ALTER COLUMN id SET DEFAULT nextval('public.closed_trades_id_seq'::regclass);


--
-- Name: corporate_actions id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.corporate_actions ALTER COLUMN id SET DEFAULT nextval('public.corporate_actions_id_seq'::regclass);


--
-- Name: data_quality_issues id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.data_quality_issues ALTER COLUMN id SET DEFAULT nextval('public.data_quality_issues_id_seq'::regclass);


--
-- Name: fills id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.fills ALTER COLUMN id SET DEFAULT nextval('public.fills_id_seq'::regclass);


--
-- Name: intraday_quotes id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.intraday_quotes ALTER COLUMN id SET DEFAULT nextval('public.intraday_quotes_id_seq'::regclass);


--
-- Name: job_runs id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.job_runs ALTER COLUMN id SET DEFAULT nextval('public.job_runs_id_seq'::regclass);


--
-- Name: llm_calls id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.llm_calls ALTER COLUMN id SET DEFAULT nextval('public.llm_calls_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: order_events id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.order_events ALTER COLUMN id SET DEFAULT nextval('public.order_events_id_seq'::regclass);


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Name: raw_payloads id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.raw_payloads ALTER COLUMN id SET DEFAULT nextval('public.raw_payloads_id_seq'::regclass);


--
-- Name: research_proposals id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.research_proposals ALTER COLUMN id SET DEFAULT nextval('public.research_proposals_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: scan_runs id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.scan_runs ALTER COLUMN id SET DEFAULT nextval('public.scan_runs_id_seq'::regclass);


--
-- Name: securities id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.securities ALTER COLUMN id SET DEFAULT nextval('public.securities_id_seq'::regclass);


--
-- Name: sim_accounts id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.sim_accounts ALTER COLUMN id SET DEFAULT nextval('public.sim_accounts_id_seq'::regclass);


--
-- Name: strategies id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategies ALTER COLUMN id SET DEFAULT nextval('public.strategies_id_seq'::regclass);


--
-- Name: strategy_stage_changes id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_stage_changes ALTER COLUMN id SET DEFAULT nextval('public.strategy_stage_changes_id_seq'::regclass);


--
-- Name: strategy_versions id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_versions ALTER COLUMN id SET DEFAULT nextval('public.strategy_versions_id_seq'::regclass);


--
-- Name: system_logs id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.system_logs ALTER COLUMN id SET DEFAULT nextval('public.system_logs_id_seq'::regclass);


--
-- Name: trade_decisions id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions ALTER COLUMN id SET DEFAULT nextval('public.trade_decisions_id_seq'::regclass);


--
-- Name: trading_restrictions id; Type: DEFAULT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_restrictions ALTER COLUMN id SET DEFAULT nextval('public.trading_restrictions_id_seq'::regclass);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: account_settings pk_account_settings; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.account_settings
    ADD CONSTRAINT pk_account_settings PRIMARY KEY (account_id);


--
-- Name: account_settings_history pk_account_settings_history; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.account_settings_history
    ADD CONSTRAINT pk_account_settings_history PRIMARY KEY (id);


--
-- Name: backtest_runs pk_backtest_runs; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT pk_backtest_runs PRIMARY KEY (id);


--
-- Name: backtest_trades pk_backtest_trades; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT pk_backtest_trades PRIMARY KEY (id);


--
-- Name: cash_ledger pk_cash_ledger; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.cash_ledger
    ADD CONSTRAINT pk_cash_ledger PRIMARY KEY (id);


--
-- Name: closed_trades pk_closed_trades; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.closed_trades
    ADD CONSTRAINT pk_closed_trades PRIMARY KEY (id);


--
-- Name: corporate_actions pk_corporate_actions; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT pk_corporate_actions PRIMARY KEY (id);


--
-- Name: daily_prices pk_daily_prices; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.daily_prices
    ADD CONSTRAINT pk_daily_prices PRIMARY KEY (security_id, trade_date);


--
-- Name: data_quality_issues pk_data_quality_issues; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.data_quality_issues
    ADD CONSTRAINT pk_data_quality_issues PRIMARY KEY (id);


--
-- Name: data_source_status pk_data_source_status; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.data_source_status
    ADD CONSTRAINT pk_data_source_status PRIMARY KEY (source, dataset);


--
-- Name: equity_snapshots pk_equity_snapshots; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.equity_snapshots
    ADD CONSTRAINT pk_equity_snapshots PRIMARY KEY (account_id, snap_date);


--
-- Name: fills pk_fills; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT pk_fills PRIMARY KEY (id);


--
-- Name: financial_statements pk_financial_statements; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.financial_statements
    ADD CONSTRAINT pk_financial_statements PRIMARY KEY (security_id, fiscal_year, fiscal_quarter, basis);


--
-- Name: institutional_trades pk_institutional_trades; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.institutional_trades
    ADD CONSTRAINT pk_institutional_trades PRIMARY KEY (security_id, trade_date);


--
-- Name: intraday_quotes pk_intraday_quotes; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.intraday_quotes
    ADD CONSTRAINT pk_intraday_quotes PRIMARY KEY (id);


--
-- Name: job_runs pk_job_runs; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.job_runs
    ADD CONSTRAINT pk_job_runs PRIMARY KEY (id);


--
-- Name: llm_calls pk_llm_calls; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.llm_calls
    ADD CONSTRAINT pk_llm_calls PRIMARY KEY (id);


--
-- Name: margin_trades pk_margin_trades; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.margin_trades
    ADD CONSTRAINT pk_margin_trades PRIMARY KEY (security_id, trade_date);


--
-- Name: market_indices pk_market_indices; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.market_indices
    ADD CONSTRAINT pk_market_indices PRIMARY KEY (index_code, trade_date);


--
-- Name: market_regimes pk_market_regimes; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.market_regimes
    ADD CONSTRAINT pk_market_regimes PRIMARY KEY (regime_date);


--
-- Name: monthly_revenues pk_monthly_revenues; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.monthly_revenues
    ADD CONSTRAINT pk_monthly_revenues PRIMARY KEY (security_id, period);


--
-- Name: notifications pk_notifications; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT pk_notifications PRIMARY KEY (id);


--
-- Name: order_events pk_order_events; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT pk_order_events PRIMARY KEY (id);


--
-- Name: orders pk_orders; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT pk_orders PRIMARY KEY (id);


--
-- Name: positions pk_positions; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT pk_positions PRIMARY KEY (account_id, security_id);


--
-- Name: raw_payloads pk_raw_payloads; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.raw_payloads
    ADD CONSTRAINT pk_raw_payloads PRIMARY KEY (id);


--
-- Name: research_proposals pk_research_proposals; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.research_proposals
    ADD CONSTRAINT pk_research_proposals PRIMARY KEY (id);


--
-- Name: reviews pk_reviews; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT pk_reviews PRIMARY KEY (id);


--
-- Name: scan_runs pk_scan_runs; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.scan_runs
    ADD CONSTRAINT pk_scan_runs PRIMARY KEY (id);


--
-- Name: securities pk_securities; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.securities
    ADD CONSTRAINT pk_securities PRIMARY KEY (id);


--
-- Name: sim_accounts pk_sim_accounts; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.sim_accounts
    ADD CONSTRAINT pk_sim_accounts PRIMARY KEY (id);


--
-- Name: stock_scores pk_stock_scores; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.stock_scores
    ADD CONSTRAINT pk_stock_scores PRIMARY KEY (scan_run_id, security_id);


--
-- Name: strategies pk_strategies; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT pk_strategies PRIMARY KEY (id);


--
-- Name: strategy_stage_changes pk_strategy_stage_changes; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_stage_changes
    ADD CONSTRAINT pk_strategy_stage_changes PRIMARY KEY (id);


--
-- Name: strategy_versions pk_strategy_versions; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT pk_strategy_versions PRIMARY KEY (id);


--
-- Name: system_logs pk_system_logs; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT pk_system_logs PRIMARY KEY (id);


--
-- Name: system_state pk_system_state; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.system_state
    ADD CONSTRAINT pk_system_state PRIMARY KEY (key);


--
-- Name: technical_indicators pk_technical_indicators; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.technical_indicators
    ADD CONSTRAINT pk_technical_indicators PRIMARY KEY (security_id, trade_date);


--
-- Name: trade_decisions pk_trade_decisions; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions
    ADD CONSTRAINT pk_trade_decisions PRIMARY KEY (id);


--
-- Name: trading_calendar pk_trading_calendar; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_calendar
    ADD CONSTRAINT pk_trading_calendar PRIMARY KEY (market, cal_date);


--
-- Name: trading_restrictions pk_trading_restrictions; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_restrictions
    ADD CONSTRAINT pk_trading_restrictions PRIMARY KEY (id);


--
-- Name: valuations pk_valuations; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.valuations
    ADD CONSTRAINT pk_valuations PRIMARY KEY (security_id, trade_date);


--
-- Name: corporate_actions uq_corp_action; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT uq_corp_action UNIQUE (security_id, ex_date, action_type);


--
-- Name: securities uq_securities_code_market; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.securities
    ADD CONSTRAINT uq_securities_code_market UNIQUE (code, market);


--
-- Name: strategies uq_strategies_code; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT uq_strategies_code UNIQUE (code);


--
-- Name: strategy_versions uq_strategy_version; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT uq_strategy_version UNIQUE (strategy_id, version);


--
-- Name: trading_restrictions uq_trading_restriction; Type: CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_restrictions
    ADD CONSTRAINT uq_trading_restriction UNIQUE (security_id, restriction_type, start_date, source);


--
-- Name: ix_backtest_trades_run; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_backtest_trades_run ON public.backtest_trades USING btree (backtest_run_id);


--
-- Name: ix_daily_prices_date; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_daily_prices_date ON public.daily_prices USING btree (trade_date);


--
-- Name: ix_dq_issues_blocking; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_dq_issues_blocking ON public.data_quality_issues USING btree (security_id, blocks_backtest);


--
-- Name: ix_dq_issues_date; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_dq_issues_date ON public.data_quality_issues USING btree (trade_date, check_name);


--
-- Name: ix_intraday_quotes_sec_time; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_intraday_quotes_sec_time ON public.intraday_quotes USING btree (security_id, quote_time);


--
-- Name: ix_job_runs_name_started; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_job_runs_name_started ON public.job_runs USING btree (job_name, started_at);


--
-- Name: ix_orders_account_created; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_orders_account_created ON public.orders USING btree (account_id, created_at);


--
-- Name: ix_raw_payloads_dataset_date; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_raw_payloads_dataset_date ON public.raw_payloads USING btree (source, dataset, data_date);


--
-- Name: ix_technical_indicators_date; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_technical_indicators_date ON public.technical_indicators USING btree (trade_date);


--
-- Name: ix_trading_restrictions_dates; Type: INDEX; Schema: public; Owner: aqt
--

CREATE INDEX ix_trading_restrictions_dates ON public.trading_restrictions USING btree (start_date, end_date);


--
-- Name: account_settings_history account_settings_history_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER account_settings_history_append_only BEFORE DELETE OR UPDATE ON public.account_settings_history FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: account_settings_history account_settings_history_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER account_settings_history_no_truncate BEFORE TRUNCATE ON public.account_settings_history FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: cash_ledger cash_ledger_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER cash_ledger_append_only BEFORE DELETE OR UPDATE ON public.cash_ledger FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: cash_ledger cash_ledger_hash_chain; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER cash_ledger_hash_chain BEFORE INSERT ON public.cash_ledger FOR EACH ROW EXECUTE FUNCTION public.aqt_hash_chain();


--
-- Name: cash_ledger cash_ledger_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER cash_ledger_no_truncate BEFORE TRUNCATE ON public.cash_ledger FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: fills fills_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER fills_append_only BEFORE DELETE OR UPDATE ON public.fills FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: fills fills_hash_chain; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER fills_hash_chain BEFORE INSERT ON public.fills FOR EACH ROW EXECUTE FUNCTION public.aqt_hash_chain();


--
-- Name: fills fills_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER fills_no_truncate BEFORE TRUNCATE ON public.fills FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: order_events order_events_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER order_events_append_only BEFORE DELETE OR UPDATE ON public.order_events FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: order_events order_events_hash_chain; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER order_events_hash_chain BEFORE INSERT ON public.order_events FOR EACH ROW EXECUTE FUNCTION public.aqt_hash_chain();


--
-- Name: order_events order_events_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER order_events_no_truncate BEFORE TRUNCATE ON public.order_events FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: orders orders_guard; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER orders_guard BEFORE DELETE OR UPDATE ON public.orders FOR EACH ROW EXECUTE FUNCTION public.aqt_order_guard();


--
-- Name: raw_payloads raw_payloads_guard; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER raw_payloads_guard BEFORE DELETE OR UPDATE ON public.raw_payloads FOR EACH ROW EXECUTE FUNCTION public.aqt_raw_payload_guard();


--
-- Name: raw_payloads raw_payloads_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER raw_payloads_no_truncate BEFORE TRUNCATE ON public.raw_payloads FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: reviews reviews_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER reviews_append_only BEFORE DELETE OR UPDATE ON public.reviews FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: reviews reviews_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER reviews_no_truncate BEFORE TRUNCATE ON public.reviews FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: strategy_stage_changes strategy_stage_changes_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER strategy_stage_changes_append_only BEFORE DELETE OR UPDATE ON public.strategy_stage_changes FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: strategy_stage_changes strategy_stage_changes_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER strategy_stage_changes_no_truncate BEFORE TRUNCATE ON public.strategy_stage_changes FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: strategy_versions strategy_versions_guard; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER strategy_versions_guard BEFORE DELETE OR UPDATE ON public.strategy_versions FOR EACH ROW EXECUTE FUNCTION public.aqt_strategy_version_guard();


--
-- Name: trade_decisions trade_decisions_append_only; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER trade_decisions_append_only BEFORE DELETE OR UPDATE ON public.trade_decisions FOR EACH ROW EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: trade_decisions trade_decisions_hash_chain; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER trade_decisions_hash_chain BEFORE INSERT ON public.trade_decisions FOR EACH ROW EXECUTE FUNCTION public.aqt_hash_chain();


--
-- Name: trade_decisions trade_decisions_no_truncate; Type: TRIGGER; Schema: public; Owner: aqt
--

CREATE TRIGGER trade_decisions_no_truncate BEFORE TRUNCATE ON public.trade_decisions FOR EACH STATEMENT EXECUTE FUNCTION public.aqt_forbid_change();


--
-- Name: account_settings fk_account_settings_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.account_settings
    ADD CONSTRAINT fk_account_settings_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: account_settings_history fk_account_settings_history_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.account_settings_history
    ADD CONSTRAINT fk_account_settings_history_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: backtest_runs fk_backtest_runs_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_runs
    ADD CONSTRAINT fk_backtest_runs_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: backtest_trades fk_backtest_trades_backtest_run_id_backtest_runs; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT fk_backtest_trades_backtest_run_id_backtest_runs FOREIGN KEY (backtest_run_id) REFERENCES public.backtest_runs(id) ON DELETE CASCADE;


--
-- Name: backtest_trades fk_backtest_trades_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT fk_backtest_trades_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: cash_ledger fk_cash_ledger_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.cash_ledger
    ADD CONSTRAINT fk_cash_ledger_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: cash_ledger fk_cash_ledger_fill_id_fills; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.cash_ledger
    ADD CONSTRAINT fk_cash_ledger_fill_id_fills FOREIGN KEY (fill_id) REFERENCES public.fills(id);


--
-- Name: closed_trades fk_closed_trades_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.closed_trades
    ADD CONSTRAINT fk_closed_trades_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: closed_trades fk_closed_trades_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.closed_trades
    ADD CONSTRAINT fk_closed_trades_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: closed_trades fk_closed_trades_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.closed_trades
    ADD CONSTRAINT fk_closed_trades_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: corporate_actions fk_corporate_actions_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT fk_corporate_actions_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: corporate_actions fk_corporate_actions_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.corporate_actions
    ADD CONSTRAINT fk_corporate_actions_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: daily_prices fk_daily_prices_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.daily_prices
    ADD CONSTRAINT fk_daily_prices_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: daily_prices fk_daily_prices_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.daily_prices
    ADD CONSTRAINT fk_daily_prices_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: data_quality_issues fk_data_quality_issues_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.data_quality_issues
    ADD CONSTRAINT fk_data_quality_issues_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: equity_snapshots fk_equity_snapshots_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.equity_snapshots
    ADD CONSTRAINT fk_equity_snapshots_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: fills fk_fills_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fk_fills_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: fills fk_fills_order_id_orders; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fk_fills_order_id_orders FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: fills fk_fills_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.fills
    ADD CONSTRAINT fk_fills_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: financial_statements fk_financial_statements_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.financial_statements
    ADD CONSTRAINT fk_financial_statements_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: financial_statements fk_financial_statements_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.financial_statements
    ADD CONSTRAINT fk_financial_statements_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: institutional_trades fk_institutional_trades_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.institutional_trades
    ADD CONSTRAINT fk_institutional_trades_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: institutional_trades fk_institutional_trades_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.institutional_trades
    ADD CONSTRAINT fk_institutional_trades_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: intraday_quotes fk_intraday_quotes_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.intraday_quotes
    ADD CONSTRAINT fk_intraday_quotes_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: margin_trades fk_margin_trades_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.margin_trades
    ADD CONSTRAINT fk_margin_trades_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: margin_trades fk_margin_trades_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.margin_trades
    ADD CONSTRAINT fk_margin_trades_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: market_indices fk_market_indices_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.market_indices
    ADD CONSTRAINT fk_market_indices_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: monthly_revenues fk_monthly_revenues_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.monthly_revenues
    ADD CONSTRAINT fk_monthly_revenues_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: monthly_revenues fk_monthly_revenues_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.monthly_revenues
    ADD CONSTRAINT fk_monthly_revenues_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: order_events fk_order_events_order_id_orders; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT fk_order_events_order_id_orders FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: orders fk_orders_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT fk_orders_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: orders fk_orders_decision_id_trade_decisions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT fk_orders_decision_id_trade_decisions FOREIGN KEY (decision_id) REFERENCES public.trade_decisions(id);


--
-- Name: orders fk_orders_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT fk_orders_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: orders fk_orders_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT fk_orders_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: positions fk_positions_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: positions fk_positions_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: positions fk_positions_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT fk_positions_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: research_proposals fk_research_proposals_candidate_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.research_proposals
    ADD CONSTRAINT fk_research_proposals_candidate_version_id_strategy_versions FOREIGN KEY (candidate_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: research_proposals fk_research_proposals_origin_review_id_reviews; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.research_proposals
    ADD CONSTRAINT fk_research_proposals_origin_review_id_reviews FOREIGN KEY (origin_review_id) REFERENCES public.reviews(id);


--
-- Name: reviews fk_reviews_llm_call_id_llm_calls; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT fk_reviews_llm_call_id_llm_calls FOREIGN KEY (llm_call_id) REFERENCES public.llm_calls(id);


--
-- Name: stock_scores fk_stock_scores_scan_run_id_scan_runs; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.stock_scores
    ADD CONSTRAINT fk_stock_scores_scan_run_id_scan_runs FOREIGN KEY (scan_run_id) REFERENCES public.scan_runs(id);


--
-- Name: stock_scores fk_stock_scores_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.stock_scores
    ADD CONSTRAINT fk_stock_scores_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: strategy_stage_changes fk_strategy_stage_changes_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_stage_changes
    ADD CONSTRAINT fk_strategy_stage_changes_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: strategy_versions fk_strategy_versions_parent_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT fk_strategy_versions_parent_version_id_strategy_versions FOREIGN KEY (parent_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: strategy_versions fk_strategy_versions_strategy_id_strategies; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.strategy_versions
    ADD CONSTRAINT fk_strategy_versions_strategy_id_strategies FOREIGN KEY (strategy_id) REFERENCES public.strategies(id);


--
-- Name: technical_indicators fk_technical_indicators_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.technical_indicators
    ADD CONSTRAINT fk_technical_indicators_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: trade_decisions fk_trade_decisions_account_id_sim_accounts; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions
    ADD CONSTRAINT fk_trade_decisions_account_id_sim_accounts FOREIGN KEY (account_id) REFERENCES public.sim_accounts(id);


--
-- Name: trade_decisions fk_trade_decisions_llm_call_id_llm_calls; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions
    ADD CONSTRAINT fk_trade_decisions_llm_call_id_llm_calls FOREIGN KEY (llm_call_id) REFERENCES public.llm_calls(id);


--
-- Name: trade_decisions fk_trade_decisions_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions
    ADD CONSTRAINT fk_trade_decisions_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: trade_decisions fk_trade_decisions_strategy_version_id_strategy_versions; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trade_decisions
    ADD CONSTRAINT fk_trade_decisions_strategy_version_id_strategy_versions FOREIGN KEY (strategy_version_id) REFERENCES public.strategy_versions(id);


--
-- Name: trading_restrictions fk_trading_restrictions_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_restrictions
    ADD CONSTRAINT fk_trading_restrictions_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: trading_restrictions fk_trading_restrictions_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.trading_restrictions
    ADD CONSTRAINT fk_trading_restrictions_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- Name: valuations fk_valuations_raw_payload_id_raw_payloads; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.valuations
    ADD CONSTRAINT fk_valuations_raw_payload_id_raw_payloads FOREIGN KEY (raw_payload_id) REFERENCES public.raw_payloads(id);


--
-- Name: valuations fk_valuations_security_id_securities; Type: FK CONSTRAINT; Schema: public; Owner: aqt
--

ALTER TABLE ONLY public.valuations
    ADD CONSTRAINT fk_valuations_security_id_securities FOREIGN KEY (security_id) REFERENCES public.securities(id);


--
-- PostgreSQL database dump complete
--

\unrestrict ydV4bz5e9fFiF0veWuud9QramnyfQryYDjerGweW1Qo47wCSFM6YbjV6R98lJpb

